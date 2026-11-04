"""Staff assistant conversations.

Kept separate from the public WhatsApp storefront conversations: different
identity (an authenticated member, not a customer), different permissions and
different retention. Rows belong to one business *and* one user; nothing here is
ever readable by another member or another business.
"""
from django.conf import settings
from django.db import models

from core.models import BusinessOwnedModel


class AssistantConversation(BusinessOwnedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_conversations")
    title = models.CharField(max_length=160, blank=True, default="")

    class Meta:
        ordering = ["-updated_at", "-pk"]
        indexes = [models.Index(fields=["business", "user", "-updated_at"], name="assistant_conv_owner_idx")]

    def __str__(self):
        return self.title or f"Conversation {self.pk}"


class AssistantMessage(BusinessOwnedModel):
    ROLE_USER, ROLE_ASSISTANT = "user", "assistant"
    ROLE_CHOICES = [(ROLE_USER, "Member"), (ROLE_ASSISTANT, "Assistant")]
    STATUS_OK, STATUS_UNAVAILABLE, STATUS_ERROR, STATUS_REFUSED, STATUS_LIMITED = (
        "ok", "unavailable", "error", "refused", "limited")
    STATUS_CHOICES = [(STATUS_OK, "Answered"), (STATUS_UNAVAILABLE, "Model unavailable"), (STATUS_ERROR, "Error"),
                      (STATUS_REFUSED, "Declined"), (STATUS_LIMITED, "Limit reached")]

    conversation = models.ForeignKey(AssistantConversation, on_delete=models.CASCADE, related_name="messages")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_messages")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OK)
    # [{"label": ..., "url": ...}] shown as source cards under the answer.
    sources = models.JSONField(default=list, blank=True)
    # Tool name + validated arguments only. Results are never stored, so a trace
    # cannot become a stale or over-broad copy of the ledger.
    tool_calls = models.JSONField(default=list, blank=True)
    scope = models.CharField(max_length=16, blank=True, default="")
    quick_fact = models.CharField(max_length=40, blank=True, default="", help_text="Key of the quick fact that produced this answer, if any.")
    suggestions = models.JSONField(default=list, blank=True, help_text="Quick-fact keys offered when a question could not be matched.")
    model_calls = models.PositiveSmallIntegerField(default=0, help_text="Language-model round trips used (quota accounting).")

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["business", "role", "created_at"], name="assistant_msg_usage_idx")]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


class AssistantPlatformSettings(models.Model):
    """Founder-level controls for the staff assistant (one row, edited in the Founder Console)."""

    assistant_enabled = models.BooleanField(default=True, help_text="Main kill switch. Off hides and blocks the assistant for every business.")
    quick_facts_enabled = models.BooleanField(default=True, help_text="Allow the built-in quick facts (read your records directly, no AI provider needed).")
    free_text_enabled = models.BooleanField(default=True, help_text="Allow AI-model answers to typed questions (needs a configured provider).")
    open_to_all_businesses = models.BooleanField(default=False, help_text="Off: only businesses switched on as pilots. On: every business admin.")
    disabled_quick_facts = models.JSONField(default=list, blank=True, help_text="Quick-fact keys switched off for every business.")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "assistant platform setting"
        verbose_name_plural = "assistant platform settings"

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class AssistantBusinessSettings(BusinessOwnedModel):
    """One business's own assistant choices. No row means all defaults (everything the platform allows)."""

    enabled = models.BooleanField(default=True, help_text="Turn the assistant on or off for this business.")
    free_text_enabled = models.BooleanField(default=True, help_text="Allow AI-model answers to typed questions.")
    disabled_quick_facts = models.JSONField(default=list, blank=True)

    custom_questions = models.JSONField(default=list, blank=True, help_text="Questions mapped to built-in record-backed reports.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business"], name="assistant_one_settings_row_per_business")]
