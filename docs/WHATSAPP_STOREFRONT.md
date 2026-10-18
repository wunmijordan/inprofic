# WhatsApp Storefront

Platform managers control whether the feature is available from **Founder Console → Platform management → Platform integrations**. While the switch is off, businesses cannot open WhatsApp setup or the inbox, the Commerce workspace hides its entry point, queued assistant or staff replies are blocked, and the WhatsApp feature is omitted from plan matrices. Tenant connections, settings, messages and webhook history are retained. Turning availability back on restores the tenant surfaces; each tenant must still have Commerce enabled and connect its own WhatsApp Business account.

WhatsApp Storefront is an optional Commerce channel. Each tenant connects its own WhatsApp Business Platform account through Meta Embedded Signup. The assistant can guide a customer from the current online catalogue through a WhatsApp basket, delivery quote, Commerce checkout and a configured public payment method. Commerce remains authoritative for product visibility, price, stock reservation, delivery quote, payment eligibility and settlement. The assistant cannot mark a payment paid or create an operational order itself.

## Connection and deployment

Configure these values in the deployment environment:

| Variable | Purpose |
| --- | --- |
| `META_WHATSAPP_APP_ID` | Meta app used for Embedded Signup. |
| `META_WHATSAPP_APP_SECRET` | Exchange signup codes and verify webhook signatures. |
| `META_WHATSAPP_CONFIG_ID` | Meta Embedded Signup configuration ID. |
| `META_WHATSAPP_VERIFY_TOKEN` | High entropy token entered in Meta webhook setup. |
| `META_WHATSAPP_GRAPH_VERSION` | Supported Graph API version. The current default is `v26.0`; check Meta's version lifecycle before upgrading or pinning it. |
| `WHATSAPP_TOKEN_ENCRYPTION_KEY` | Dedicated secret of at least 32 characters used to encrypt connected tenant access tokens at rest. Retain it across deploys and back it up separately. |
| `WHATSAPP_AI_API_KEY` | Optional OpenAI-compatible API key. Leave unset to use deterministic replies. |
| `WHATSAPP_AI_MODEL` | Model identifier used by the optional AI provider. |
| `WHATSAPP_AI_BASE_URL` | OpenAI-compatible API base URL. |

### Getting Meta credentials

Create or select the platform's app in [Meta for Developers](https://developers.facebook.com/apps/) and choose the business-messaging/WhatsApp setup. The `App ID` and `App Secret` are in **App settings → Basic**. Create an Embedded Signup configuration in the app's WhatsApp/Business Login setup and put its configuration ID in `META_WHATSAPP_CONFIG_ID`. Set a high-entropy webhook verify token of your choice in Meta's webhook configuration and use the same value for `META_WHATSAPP_VERIFY_TOKEN`; set the callback to `https://<your-domain>/api/v1/whatsapp/webhook/`, subscribe the app to the WhatsApp `messages` field, and keep it reachable over HTTPS. Set `META_WHATSAPP_GRAPH_VERSION` to a Graph version Meta currently supports.

These are platform integration credentials, not each tenant's WhatsApp Business credentials. The app ID identifies the INPROFIC integration; the app secret lets the server exchange the short-lived Embedded Signup code; the configuration ID launches the configured signup experience; and the webhook verify token authenticates Meta's initial callback challenge. After a business owner completes signup, INPROFIC stores that tenant's WABA ID, phone-number ID and access token (encrypted with `WHATSAPP_TOKEN_ENCRYPTION_KEY`) to operate only the number they authorized. Each business still controls its own Meta Business assets and grants the platform app access during signup. The platform app credentials are needed because every tenant authorizes the same deployed integration; tenant-owned Meta accounts do not identify or authenticate that integration to Meta.

For deployment to businesses outside the app's roles, follow Meta's [Embedded Signup documentation](https://www.postman.com/meta/whatsapp-business-platform/documentation/du6gzjv/embedded-signup): Meta says release requires App Review and Advanced Access for `business_management` and `whatsapp_business_management`. The Cloud API's send-message endpoint also requires `whatsapp_business_messaging`; request the access your signup and messaging flows actually use. Complete applicable business verification and Tech Provider onboarding in Meta Business settings, configure Embedded Signup, and submit the real user flow for review. Verify Meta's current requirements before production release, since they can change.

Run the existing scheduled job command (`python manage.py run_scheduled_jobs`) frequently enough to process queued webhook events and outbound replies. Webhooks are signature checked, persisted before acknowledgement, deduplicated, and processed by `process_whatsapp_queue`. The queue can also be run directly during operations with `python manage.py process_whatsapp_queue`.

Tenant tokens are encrypted with Fernet using the dedicated deployment secret. Losing or rotating that secret without re-encrypting saved values makes those credentials unusable; reconnect affected numbers after recovery. Disconnecting a tenant clears the saved token and stops assistant replies while retaining conversation and audit history.

## Customer and staff behavior

- A business administrator must have Commerce enabled to open WhatsApp settings or connect a number.
- The assistant reads only the tenant's published, online catalogue and current Commerce prices/availability. Deterministic product replies use those live records; the optional AI prompt receives only the business's approved guidance and recent chat context. AI is not used to decide catalogue commands, prices, basket contents, quotes or payment status.
- Simple products can be added with `ADD <menu-number> [quantity]`, reviewed with `BASKET`, and checked out with `CHECKOUT`. The chat collects the order name, pickup/delivery choice, full delivery address and the customer's choice among active Commerce delivery quotes. It then creates a `CommerceCheckoutSession` with source `whatsapp`, revalidating the published products and current prices and reserving stock through the existing checkout service.
- The chat offers only payment methods returned by `eligible_payment_methods(..., surface="public")`. It starts payment through `initiate_payment`; gateway customers receive the provider authorization link. Native manual transfer shows the selected account and reference, then sends the customer to the hosted checkout to upload proof. Customers can return to WhatsApp and ask `STATUS`; only Commerce's verified payment record determines confirmation.
- The hosted checkout is the fallback for products with variant/bulk/individual options, missing or expired delivery quotes, provider-required email collection, transfer-proof upload, or any step the assistant cannot safely complete. It uses the same checkout and payment records, not a second order path.
- Customers can ask for a person. The conversation enters human takeover, and an administrator can reply from the WhatsApp inbox.
- Only replies to inbound messages are supported. Messages are sent inside Meta's customer-service window; expired windows require an approved template, which this implementation does not send.
- Do not use this feature for unsolicited campaigns. Obtain and honor customer consent in line with Meta policies and applicable privacy requirements.
- If AI is enabled, the customer's recent messages, business name and approved instructions are sent to the configured AI provider. Configure a provider only after the business has approved that data flow. Without an AI API key, or if the provider is unavailable, deterministic replies and human handoff remain available.
- The current WhatsApp Business Solution Terms permit AI Providers as third-party service providers subject to restrictions, including no use of Business Solution Data to train or improve general AI models. Keep this feature limited to ancillary commerce support and use a provider whose terms and configuration meet those restrictions. Recheck Meta's terms and applicable local/regional rules before enabling it for production tenants. See [WhatsApp Business Solution Terms](https://www.whatsapp.com/legal/business-solution-terms).

## Application contracts

There is no public customer API that bypasses the storefront checkout contract. The connection and conversation inbox are authenticated, tenant-scoped application views. A WhatsApp-origin checkout is internally tagged with source `whatsapp`; it runs through the existing checkout/payment service boundary. Meta calls only:

```text
GET  /api/v1/whatsapp/webhook/?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
POST /api/v1/whatsapp/webhook/  (X-Hub-Signature-256 required)
```

Inbound message and delivery-status events are stored in the internal webhook outbox before a `200` response. Events are deduplicated by Meta message ID/status tuple. A headless website can continue to use the Commerce API documented in [COMMERCE_INTEGRATION.md](COMMERCE_INTEGRATION.md); WhatsApp does not create a second product, order, payment or fulfilment contract.

## Provider naming

The current Shipbubble Terms of Use say its marks, including the Shipbubble word mark, may not be used without written authorization and the Merchant Terms restrict statements acknowledging a relationship without prior written consent. API credentials are a separate technical requirement. Before publicly naming Shipbubble in a plan matrix or marketing material, obtain written permission or specific written guidance from Shipbubble. This is a platform-level brand clearance question; it is not a legal authorization that each tenant should be told to obtain. See [Shipbubble Terms and Conditions](https://shipbubble.com/terms-and-conditions) and the [Shipbubble API getting-started guide](https://docs.shipbubble.com/get-started).
