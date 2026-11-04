"""Provider-free tests for webhook security, tenant routing and queue recovery."""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import BusinessModuleAccess
from commerce.models import CommerceIntake, CommerceSettings, StorefrontProduct
from core.context import set_current_business
from core.models import Business
from inventory.models import FinishedGood
from .assistant import _create_whatsapp_checkout, _checkout_link
from .meta import MetaWhatsAppError
from .models import WhatsAppConnection, WhatsAppConversation, WhatsAppMessage, WhatsAppWebhookEvent
from .security import encrypt_access_token, decrypt_access_token, WhatsAppTokenError
from .services import persist_webhook_events, process_one_event, process_assistant_replies, process_outbound_messages


@override_settings(META_WHATSAPP_APP_SECRET='local-webhook-secret', META_WHATSAPP_VERIFY_TOKEN='local-verify-token',
                   WHATSAPP_TOKEN_ENCRYPTION_KEY='local-encryption-secret-at-least-32-characters', SITE_URL='https://audit.example.test')
class WhatsAppIntegrationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='WhatsApp Retail', slug='whatsapp-retail', vertical='retail')
        BusinessModuleAccess.objects.update_or_create(business=self.business, module='commerce', defaults={'enabled': True})
        CommerceSettings.raw_objects.create(business=self.business, enabled=True, hosted_storefront_enabled=True)
        self.connection = WhatsAppConnection.raw_objects.create(
            business=self.business, waba_id='100', phone_number_id='101', encrypted_access_token=encrypt_access_token('private-token'),
            assistant_enabled=True,
        )
        self.conversation = WhatsAppConversation.raw_objects.create(
            business=self.business, connection=self.connection, customer_wa_id='2348000000000', customer_name='Buyer',
            last_customer_message_at=timezone.now(),
        )
        self.channel = patch('whatsapp_storefront.services._commerce_channel_enabled', return_value=True)
        self.channel.start()
        self.addCleanup(self.channel.stop)
        self.addCleanup(set_current_business, None)

    def payload(self, phone='101', message_id='provider-inbound-1'):
        return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': phone}, 'contacts': [{'profile': {'name': 'Buyer'}}],
            'messages': [{'id': message_id, 'from': '2348000000000', 'type': 'text', 'text': {'body': 'MENU'}}],
        }}]}]}

    def outbound(self, **overrides):
        values = dict(business=self.business, conversation=self.conversation, direction='out', body='Reply',
                      status='queued', next_attempt_at=timezone.now())
        values.update(overrides)
        return WhatsAppMessage.raw_objects.create(**values)

    def test_anonymous_verification_requires_the_configured_token(self):
        query = {'hub.mode': 'subscribe', 'hub.verify_token': 'local-verify-token', 'hub.challenge': 'challenge'}
        response = self.client.get('/api/v1/whatsapp/webhook/', query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'challenge')
        query['hub.verify_token'] = 'wrong'
        self.assertEqual(self.client.get('/api/v1/whatsapp/webhook/', query).status_code, 403)

    def test_webhook_rejects_unsigned_messages_and_accepts_a_valid_signature(self):
        raw = json.dumps(self.payload()).encode()
        url = '/api/v1/whatsapp/webhook/'
        self.assertEqual(self.client.post(url, raw, content_type='application/json').status_code, 403)
        signature = 'sha256=' + hmac.new(b'local-webhook-secret', raw, hashlib.sha256).hexdigest()
        response = self.client.post(url, raw, content_type='application/json', HTTP_X_HUB_SIGNATURE_256=signature)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WhatsAppWebhookEvent.objects.count(), 1)

    def test_duplicate_webhooks_leave_the_transaction_usable(self):
        self.assertEqual(persist_webhook_events(self.payload()), 1)
        self.assertEqual(persist_webhook_events(self.payload()), 0)
        self.assertEqual(WhatsAppWebhookEvent.objects.count(), 1)

    def test_event_routing_uses_the_connected_phone_tenant_and_is_idempotent(self):
        other = Business.objects.create(name='Other', slug='other-whatsapp')
        set_current_business(other)
        persist_webhook_events(self.payload())
        event = WhatsAppWebhookEvent.objects.get()
        process_one_event(event)
        process_one_event(event)
        message = WhatsAppMessage.raw_objects.get(direction='in')
        self.assertEqual(message.business_id, self.business.pk)
        self.assertEqual(message.conversation.business_id, self.business.pk)
        self.assertEqual(WhatsAppMessage.raw_objects.filter(direction='out').count(), 1)

    def test_unknown_phone_is_retired_after_bounded_retries(self):
        persist_webhook_events(self.payload(phone='999'))
        event = WhatsAppWebhookEvent.objects.get()
        for _ in range(3):
            process_one_event(event)
        event.refresh_from_db()
        self.assertEqual(event.attempts, 3)
        self.assertIsNotNone(event.processed_at)
        self.assertFalse(WhatsAppMessage.raw_objects.exists())

    @patch('whatsapp_storefront.services.send_text')
    def test_expired_chat_window_does_not_send_raw_text(self, send):
        self.conversation.last_customer_message_at = timezone.now() - timedelta(hours=25)
        self.conversation.save(update_fields=['last_customer_message_at'])
        message = self.outbound()
        process_outbound_messages()
        message.refresh_from_db()
        self.assertEqual(message.status, 'failed')
        self.assertIn('template', message.error)
        send.assert_not_called()

    @patch('whatsapp_storefront.services.send_text')
    def test_successful_send_is_not_repeated(self, send):
        send.return_value = {'messages': [{'id': 'provider-outbound-1'}]}
        message = self.outbound()
        self.assertEqual(process_outbound_messages(), 1)
        self.assertEqual(process_outbound_messages(), 0)
        send.assert_called_once()
        message.refresh_from_db()
        self.assertEqual(message.status, 'sent')

    @patch('whatsapp_storefront.services.send_text', side_effect=MetaWhatsAppError('Unavailable'))
    def test_send_failure_is_bounded_and_marks_the_connection_for_review(self, send):
        message = self.outbound(attempts=3)
        process_outbound_messages()
        message.refresh_from_db()
        self.connection.refresh_from_db()
        self.assertEqual(message.status, 'failed')
        self.assertEqual(message.attempts, 4)
        self.assertEqual(self.connection.status, 'error')

    @patch('whatsapp_storefront.services.build_reply', return_value='Menu reply')
    def test_assistant_reply_placeholder_is_processed_once(self, build):
        persist_webhook_events(self.payload())
        process_one_event(WhatsAppWebhookEvent.objects.get())
        self.assertEqual(process_assistant_replies(), 1)
        self.assertEqual(process_assistant_replies(), 0)
        build.assert_called_once()
        self.assertEqual(WhatsAppMessage.raw_objects.get(direction='out').body, 'Menu reply')

    def test_checkout_handoff_uses_only_this_tenants_catalogue(self):
        good = FinishedGood.raw_objects.create(business=self.business, name='Item', unit='piece', stock=10, selling_price=25)
        product = StorefrontProduct.raw_objects.create(business=self.business, finished_good=good, published=True)
        key = str(product.public_id)
        self.conversation.state = {'basket': {key: {'quantity': '2'}}, 'customer_name': 'Buyer'}
        self.conversation.save(update_fields=['state'])
        catalogue = [{'id': key, 'name': 'Item', 'price': '25', 'has_options': False}]
        checkout = _create_whatsapp_checkout(self.conversation, catalogue)
        self.assertEqual(checkout.business_id, self.business.pk)
        self.assertEqual(checkout.source, CommerceIntake.SOURCE_WHATSAPP)
        self.assertEqual(checkout.amount, 50)
        self.assertFalse(CommerceIntake.raw_objects.filter(business=self.business).exists())
        self.assertTrue(_checkout_link(self.conversation, checkout).startswith('https://audit.example.test/'))
        other = Business.objects.create(name='Foreign', slug='foreign-whatsapp')
        self.conversation.business = other
        with self.assertRaises(ValidationError):
            _create_whatsapp_checkout(self.conversation, catalogue)

    def test_access_tokens_are_encrypted_and_wrong_keys_are_rejected(self):
        encrypted = self.connection.encrypted_access_token
        self.assertNotIn('private-token', encrypted)
        self.assertEqual(decrypt_access_token(encrypted), 'private-token')
        with override_settings(WHATSAPP_TOKEN_ENCRYPTION_KEY='different-local-key-at-least-32-characters'):
            with self.assertRaises(WhatsAppTokenError):
                decrypt_access_token(encrypted)
