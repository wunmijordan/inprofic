from django import forms
from django.contrib.auth import password_validation
from core.models import Business
from .models import CustomUser, Role, RoleModulePermission, UserBusiness, UserModulePermission, SubscriptionPlan, SubscriptionPromotion, MarketingPromoCampaign, SubscriptionPolicySettings, PlatformMailTemplate, BusinessTrialIdentity, MarketingTrustSettings, MarketingTrustLogo
from .services import ensure_permissions, is_business_admin, seed_business_roles

CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"

# POS can be a dedicated cashier-only role or a supplemental per-user permission on an existing role.
USER_OVERRIDE_MODULES = tuple(RoleModulePermission.MODULE_CHOICES)

def _trial_phone_key(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())

def _trial_name_key(value):
    return " ".join((value or "").strip().casefold().split())


class BusinessSignupForm(forms.Form):
    business_name = forms.CharField(max_length=120, label="Business name")
    vertical = forms.ChoiceField(label="Service", choices=Business.VERTICAL_CHOICES)
    fullname = forms.CharField(max_length=160, label="Full Name")
    username = forms.CharField(max_length=80)
    email = forms.EmailField()
    phone = forms.CharField(max_length=30, required=False)
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = CLS

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already in use. Sign in if it belongs to you.")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        # Trial eligibility is tied only to currently-existing tenant records.
        # A hard-deleted tenant is deliberately not consulted here.
        if (BusinessTrialIdentity.objects.filter(email_key=email.casefold()).exists()
                or UserBusiness.objects.filter(business__isnull=False, role__key=CustomUser.ROLE_BUSINESS_ADMIN, user__email__iexact=email).exists()):
            raise forms.ValidationError("This email has already been used for an INPROFIC business and is not eligible for another free trial.")
        return email

    def clean(self):
        cleaned = super().clean()
        business_name = (cleaned.get("business_name") or "").strip()
        if business_name and (
            Business.objects.filter(name__iexact=business_name).exists()
            or BusinessTrialIdentity.objects.filter(business_name_key=_trial_name_key(business_name)).exists()
        ):
            self.add_error("business_name", "A current or previously subscribed INPROFIC business already used this exact name and it is not eligible for another free trial.")
        phone = (cleaned.get("phone") or "").strip()
        if phone:
            normalized = _trial_phone_key(phone)
            if normalized:
                current_phones = CustomUser.objects.filter(
                    business_memberships__business__isnull=False,
                    business_memberships__role__key=CustomUser.ROLE_BUSINESS_ADMIN,
                ).exclude(phone="").values_list("phone", flat=True).distinct()
                if (BusinessTrialIdentity.objects.filter(phone_key=normalized).exists()
                        or any(_trial_phone_key(value) == normalized for value in current_phones)):
                    self.add_error("phone", "This phone number has already been used for an INPROFIC business and is not eligible for another free trial.")
        password = cleaned.get("password1")
        if password and password != cleaned.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        if password:
            candidate = CustomUser(
                username=cleaned.get("username", ""),
                fullname=cleaned.get("fullname", ""),
                email=cleaned.get("email", ""),
            )
            password_validation.validate_password(password, candidate)
        return cleaned


class UserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False, help_text="Required for a new user; leave blank when editing to keep the current password.")
    role = forms.ModelChoiceField(queryset=Role.objects.none(), empty_label=None)
    class Meta:
        model = CustomUser
        fields = ["fullname", "username", "email", "phone", "role", "password", "is_active"]

    def __init__(self, *args, business, actor, membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.actor = actor
        self.membership = membership
        seed_business_roles(business)
        qs = Role.objects.filter(business=business, active=True).order_by("is_system", "name")
        if not actor.is_superuser:
            qs = qs.exclude(key=CustomUser.ROLE_SUPERUSER)
            # A Business Admin is delegated authority for ordinary business users,
            # but only the global superuser may appoint another Business Admin.
            if is_business_admin(actor, business):
                qs = qs.exclude(key=CustomUser.ROLE_BUSINESS_ADMIN)
            # Roles the superuser marked hidden (e.g. a demo/review role) are
            # never assignable by a Business Admin, even by URL/ID guessing.
            qs = qs.exclude(visible_to_admin=False)
        self.fields["role"].queryset = qs
        for f in self.fields.values():
            f.widget.attrs["class"] = CLS
        self.fields["is_active"].label = "Can sign in"
        self.fields["is_active"].help_text = "Turn this off to pause this user's access without deleting their account."
        if membership:
            self.fields["role"].initial = membership.role_id

    def clean_password(self):
        value = self.cleaned_data.get("password")
        if value and self.instance.pk:
            password_validation.validate_password(value, self.instance)
        return value

    def clean(self):
        c = super().clean()
        if not c.get("email") and not c.get("phone"):
            raise forms.ValidationError("Provide at least an email address or phone number.")
        if not self.instance.pk and not c.get("password"):
            self.add_error("password", "A password is required for a new user.")
        role = c.get("role")
        if role and role.key == CustomUser.ROLE_SUPERUSER and not self.actor.is_superuser:
            self.add_error("role", "Only the global superuser can create or assign a Superuser role.")
        if role and role.key == CustomUser.ROLE_BUSINESS_ADMIN and not self.actor.is_superuser:
            self.add_error("role", "Only the global superuser can appoint a Business Admin.")
        return c

    def save(self, commit=True):
        obj = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            obj.set_password(password)
        if commit:
            obj.save()
        return obj


class FounderUserManagementForm(forms.ModelForm):
    new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = CustomUser
        fields = [
            "fullname", "username", "email", "phone",
            "is_active", "is_staff", "platform_mail_access", "is_superuser",
        ]

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        for field in self.fields.values():
            field.widget.attrs["class"] = CLS
        for name in ("is_active", "is_staff", "platform_mail_access", "is_superuser"):
            self.fields[name].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"
        self.fields["is_active"].help_text = "Paused accounts cannot sign in, but their history remains intact."
        if not self.instance.pk:
            self.fields["new_password"].required = True
        self.fields["is_staff"].help_text = "Django administration access flag. Leave this off for mailing-only project users."
        self.fields["platform_mail_access"].help_text = "Allows this project-level user to use only the INPROFIC mailing workspace and business mailing analytics."
        self.fields["is_superuser"].help_text = "Grants unrestricted platform access. Use only for trusted founders."

    def clean(self):
        cleaned = super().clean()
        if self.actor and self.instance.pk == self.actor.pk:
            for field in ("is_active", "is_staff", "is_superuser"):
                if not cleaned.get(field):
                    self.add_error(field, "You cannot remove this access from the account you are currently using.")
        password = cleaned.get("new_password")
        if not self.instance.pk and not password:
            self.add_error("new_password", "A password is required for a new project-level user.")
        if password:
            password_validation.validate_password(password, self.instance)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("new_password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


class PermissionMatrixForm(forms.Form):
    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.membership = membership
        ensure_permissions(membership)
        for module, label in USER_OVERRIDE_MODULES:
            p = membership.module_permissions.get(module=module)
            role_p = membership.role.module_permissions.filter(module=module).first()
            self.fields[f"{module}_view"] = forms.BooleanField(required=False, label=f"{label}: View", initial=p.can_view if p.can_view is not None else (role_p.can_view if role_p else False))
            self.fields[f"{module}_edit"] = forms.BooleanField(required=False, label=f"{label}: Edit", initial=p.can_edit if p.can_edit is not None else (role_p.can_edit if role_p else False))
            self.fields[f"{module}_view"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"
            self.fields[f"{module}_edit"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"

    def save(self):
        for module, _ in USER_OVERRIDE_MODULES:
            UserModulePermission.objects.update_or_create(
                membership=self.membership, module=module,
                defaults={"can_view": self.cleaned_data.get(f"{module}_view", False),
                          "can_edit": self.cleaned_data.get(f"{module}_edit", False)}
            )


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ["name", "active", "visible_to_admin"]
        widgets = {
            "name": forms.TextInput(attrs={"class": CLS}),
            "active": forms.CheckboxInput(attrs={"class": "h-4 w-4 accent-[#8f172d]"}),
            "visible_to_admin": forms.CheckboxInput(attrs={"class": "h-4 w-4 accent-[#8f172d]"}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["active"].label = "Role available"
        self.fields["active"].help_text = "Turn this off to stop assigning and using this role while keeping it saved."
        self.fields["visible_to_admin"].label = "Business admins can assign this role"
        # Only the global superuser may create/keep a role hidden from the
        # Business Admin. Anyone else never even sees the field, so a role
        # they create or edit always stays at the model default (visible).
        if not (actor and actor.is_superuser):
            del self.fields["visible_to_admin"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Role name is required.")
        return name


class RolePermissionForm(forms.Form):
    def __init__(self, *args, role, **kwargs):
        super().__init__(*args, **kwargs)
        self.role = role
        for module, label in RoleModulePermission.MODULE_CHOICES:
            p = role.module_permissions.filter(module=module).first()
            self.fields[f"{module}_view"] = forms.BooleanField(required=False, label=f"{label}: View", initial=p.can_view if p else False)
            self.fields[f"{module}_edit"] = forms.BooleanField(required=False, label=f"{label}: Edit", initial=p.can_edit if p else False)
            self.fields[f"{module}_view"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"
            self.fields[f"{module}_edit"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"

    def save(self):
        for module, _ in RoleModulePermission.MODULE_CHOICES:
            RoleModulePermission.objects.update_or_create(
                role=self.role, module=module,
                defaults={"can_view": self.cleaned_data.get(f"{module}_view", False),
                          "can_edit": self.cleaned_data.get(f"{module}_edit", False)}
            )


class SubscriptionPromotionForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPromotion
        fields = ["applies_to_all_plans", "plan", "reason", "discount_type", "discount_value", "billing_cycle", "starts_at", "ends_at"]
        widgets = {
            "applies_to_all_plans": forms.CheckboxInput(attrs={"data-promo-all-plans": "1"}),
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id")
        self.fields["plan"].required = False
        self.fields["plan"].help_text = "Choose one plan, or enable All plans above."
        self.fields["applies_to_all_plans"].label = "Apply this promotion to all plans"
        for name, field in self.fields.items():
            if name != "applies_to_all_plans":
                field.widget.attrs["class"] = CLS

    def clean(self):
        from decimal import Decimal
        from django.db.models import Q
        cleaned = super().clean()
        plan = cleaned.get("plan")
        all_plans = bool(cleaned.get("applies_to_all_plans"))
        kind = cleaned.get("discount_type")
        value = cleaned.get("discount_value")
        starts = cleaned.get("starts_at")
        ends = cleaned.get("ends_at")
        billing_cycle = cleaned.get("billing_cycle") or SubscriptionPromotion.CYCLE_BOTH
        if all_plans:
            cleaned["plan"] = None
            plan = None
        elif not plan:
            self.add_error("plan", "Choose a plan, or apply this promotion to all plans.")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", "Promotion expiry must be after its start time.")
        if value is not None and value <= 0:
            self.add_error("discount_value", "Enter a discount greater than zero.")
        if kind == SubscriptionPromotion.DISCOUNT_PERCENT and value is not None and value >= Decimal("100"):
            self.add_error("discount_value", "Percentage discounts must leave a payable amount (use less than 100%).")
        target_plans = list(SubscriptionPlan.objects.filter(active=True)) if all_plans else ([plan] if plan else [])
        if kind == SubscriptionPromotion.DISCOUNT_AMOUNT and value is not None:
            invalid = [p.name for p in target_plans if value >= p.monthly_price]
            if invalid:
                self.add_error("discount_value", "A fixed discount must leave a payable amount on every targeted plan: " + ", ".join(invalid))
        if starts and ends and target_plans:
            overlap = SubscriptionPromotion.objects.filter(
                active=True, starts_at__lt=ends, ends_at__gt=starts
            ).filter(
                Q(applies_to_all_plans=True) |
                Q(plan_id__in=[p.pk for p in target_plans])
            )
            if all_plans:
                overlap = SubscriptionPromotion.objects.filter(
                    active=True, starts_at__lt=ends, ends_at__gt=starts
                )
            if self.instance.pk:
                overlap = overlap.exclude(pk=self.instance.pk)
            def cycles_overlap(existing):
                return (
                    billing_cycle == SubscriptionPromotion.CYCLE_BOTH
                    or existing.billing_cycle == SubscriptionPromotion.CYCLE_BOTH
                    or existing.billing_cycle == billing_cycle
                )
            if any(cycles_overlap(existing) for existing in overlap.only("billing_cycle")):
                self.add_error(None, "Another active/scheduled promotion overlaps at least one targeted plan and billing cycle in that period.")
        return cleaned


class MarketingPromoCampaignForm(forms.ModelForm):
    class Meta:
        model = MarketingPromoCampaign
        fields = [
            "name", "promotion", "content_html", "cta_label", "animation_style", "theme",
            "priority", "image", "video", "video_poster", "media_alt", "active",
        ]
        widgets = {
            "content_html": forms.HiddenInput(attrs={"data-campaign-html": "1"}),
            "image": forms.ClearableFileInput(attrs={"accept": "image/*"}),
            "video": forms.ClearableFileInput(attrs={"accept": "video/mp4,video/webm,video/quicktime"}),
            "video_poster": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def __init__(self, *args, **kwargs):
        from django.db.models import Q
        from django.utils import timezone
        super().__init__(*args, **kwargs)
        promo_qs = SubscriptionPromotion.objects.select_related("plan").filter(
            Q(active=True, ends_at__gt=timezone.now()) |
            Q(pk=getattr(self.instance, "promotion_id", None))
        ).order_by("-starts_at", "plan__monthly_price", "id")
        self.fields["promotion"].queryset = promo_qs
        self.fields["promotion"].label_from_instance = lambda obj: f"{obj.target_label} — {obj.reason}"
        self.fields["content_html"].required = True
        self.fields["content_html"].help_text = "Use the editor below. Only INPROFIC's bundled fonts and safe text formatting are retained."
        self.fields["active"].label = "Show campaign"
        self.fields["active"].help_text = "Turn this off to keep the campaign saved without showing it to visitors."
        for name, field in self.fields.items():
            if name != "content_html":
                field.widget.attrs.setdefault("class", CLS)

    def clean_content_html(self):
        from django.utils.html import strip_tags
        from .marketing_campaigns import sanitize_campaign_html
        value = sanitize_campaign_html(self.cleaned_data.get("content_html") or "")
        if not strip_tags(value).strip():
            raise forms.ValidationError("Add campaign copy before saving.")
        return value

    def clean(self):
        cleaned = super().clean()
        image = cleaned.get("image")
        video = cleaned.get("video")
        poster = cleaned.get("video_poster")
        if image and video:
            self.add_error("video", "Use either an image or a video for one campaign, not both.")
        for field_name, upload, limit_mb in (("image", image, 10), ("video_poster", poster, 10), ("video", video, 80)):
            if upload and hasattr(upload, "size") and upload.size > limit_mb * 1024 * 1024:
                self.add_error(field_name, f"Keep this upload below {limit_mb} MB.")
        return cleaned


class MarketingTrustSettingsForm(forms.ModelForm):
    class Meta:
        model = MarketingTrustSettings
        fields = ["enabled"]
        labels = {"enabled": "Show trusted-business strip on the marketing page"}
        help_texts = {"enabled": "The counter includes every business, including trials. Automatic logos are shown only for currently paid businesses."}
        widgets = {"enabled": forms.CheckboxInput(attrs={"class": "h-4 w-4 accent-[#8f172d]"})}


class MarketingTrustLogoForm(forms.ModelForm):
    class Meta:
        model = MarketingTrustLogo
        fields = ["name", "logo", "sort_order"]
        labels = {"name": "Business name", "logo": "Logo file", "sort_order": "Display order"}
        help_texts = {"logo": "Upload the original PNG, JPG or WebP. INPROFIC keeps its natural proportions.", "sort_order": "Lower numbers appear first."}
        widgets = {
            "name": forms.TextInput(attrs={"class": CLS, "placeholder": "Business name"}),
            "logo": forms.ClearableFileInput(attrs={"class": CLS, "accept": ".png,.jpg,.jpeg,.webp,image/*"}),
            "sort_order": forms.NumberInput(attrs={"class": CLS, "min": 0}),
        }

    def clean_logo(self):
        upload = self.cleaned_data["logo"]
        if getattr(upload, "size", 0) > 10 * 1024 * 1024:
            raise forms.ValidationError("Choose a logo file no larger than 10 MB.")
        return upload


class AddSubscriptionServiceForm(forms.Form):
    business_name = forms.CharField(max_length=120, widget=forms.TextInput(attrs={"class": CLS}))
    service_type = forms.ChoiceField(choices=Business.VERTICAL_CHOICES, widget=forms.Select(attrs={"class": CLS}))


class FounderGrantForm(forms.Form):
    business = forms.ModelChoiceField(queryset=Business.objects.none(), widget=forms.Select(attrs={"class": CLS}))
    plan = forms.ModelChoiceField(queryset=SubscriptionPlan.objects.none(), widget=forms.Select(attrs={"class": CLS}))
    note = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"class": CLS}))

    def __init__(self, *args, **kwargs):
        from .models import SubscriptionPlan
        super().__init__(*args, **kwargs)
        self.fields["business"].queryset = Business.objects.order_by("name")
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id")


class SubscriptionTrialPolicyForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPolicySettings
        fields = ["general_trial_days"]
        labels = {"general_trial_days": "General free trial (days)"}
        widgets = {"general_trial_days": forms.NumberInput(attrs={"class": CLS, "min": 1, "max": 3650})}

    def clean_general_trial_days(self):
        value = int(self.cleaned_data.get("general_trial_days") or 0)
        if not 1 <= value <= 3650:
            raise forms.ValidationError("Choose a trial length between 1 and 3,650 days.")
        return value


class FounderTrialGrantForm(forms.Form):
    business = forms.ModelChoiceField(
        queryset=Business.objects.none(), label="Business", widget=forms.Select(attrs={"class": CLS})
    )
    plan = forms.ModelChoiceField(
        queryset=SubscriptionPlan.objects.none(), label="Trial plan", widget=forms.Select(attrs={"class": CLS})
    )
    days = forms.IntegerField(
        min_value=1, max_value=3650, label="Additional trial days",
        widget=forms.NumberInput(attrs={"class": CLS, "min": 1, "max": 3650}),
    )
    note = forms.CharField(
        required=False, max_length=255, label="Note",
        widget=forms.TextInput(attrs={"class": CLS, "placeholder": "Optional founder note"}),
    )

    def __init__(self, *args, default_days=30, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["business"].queryset = Business.objects.order_by("name")
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id")
        if not self.is_bound:
            self.fields["days"].initial = max(1, int(default_days or 30))

    def clean_plan(self):
        plan = self.cleaned_data["plan"]
        if plan.is_free_forever:
            raise forms.ValidationError("A free-forever plan does not need a trial window.")
        return plan


class BusinessRestoreForm(forms.Form):
    target_business = forms.ModelChoiceField(
        queryset=Business.objects.none(),
        label="Destination business",
        help_text="Choose the business workspace that should receive the restored records.",
        widget=forms.Select(attrs={"class": CLS}),
    )
    database = forms.FileField(
        label="Business backup file",
        help_text="Upload an INPROFIC backup file or a PythonAnywhere backup. Previewing checks what can be restored without changing your current records.",
        widget=forms.ClearableFileInput(attrs={"accept": ".json,.sqlite,.sqlite3,.db,application/json,application/vnd.sqlite3,application/octet-stream", "class": CLS}),
    )
    source_business_id = forms.IntegerField(
        required=False,
        min_value=1,
        label="Business number in backup",
        help_text="Leave this blank when the backup contains one business. If several businesses are present, the preview will list their numbers.",
        widget=forms.NumberInput(attrs={"class": CLS, "placeholder": "Auto-detect when possible"}),
    )
    confirmation = forms.CharField(
        required=False,
        label="Restore confirmation",
        help_text="Required only when restoring. Type RESTORE followed by the public business address, for example RESTORE my-business.",
        widget=forms.TextInput(attrs={"class": CLS, "autocomplete": "off"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_business"].queryset = Business.objects.order_by("name", "id")

    def clean_database(self):
        uploaded = self.cleaned_data["database"]
        if getattr(uploaded, "size", 0) > 200 * 1024 * 1024:
            raise forms.ValidationError("Choose a backup file no larger than 200 MB.")
        return uploaded

class PlatformMailContentFormMixin:
    personalization_help = (
        "Available: {{ business_name }}, {{ recipient_name }}, "
        "{{ service }} and {{ plan_name }}."
    )

    def clean_subject(self):
        from .mailing_content import validate_personalization

        value = validate_personalization((self.cleaned_data.get("subject") or "").strip())
        if "\n" in value or "\r" in value:
            raise forms.ValidationError("Keep the email subject on one line.")
        return value

    def clean_heading(self):
        from .mailing_content import validate_personalization

        value = validate_personalization((self.cleaned_data.get("heading") or "").strip())
        if "\n" in value or "\r" in value:
            raise forms.ValidationError("Keep the email heading on one line.")
        return value

    def clean_body_html(self):
        from .mailing_content import clean_mail_html

        return clean_mail_html(self.cleaned_data.get("body_html") or "")

    def clean(self):
        cleaned = super().clean()
        label = (cleaned.get("cta_label") or "").strip()
        url = (cleaned.get("cta_url") or "").strip()
        if bool(label) != bool(url):
            missing = "cta_url" if label else "cta_label"
            self.add_error(missing, "Provide both the button label and its URL, or leave both blank.")
        return cleaned


class PlatformMailTemplateForm(PlatformMailContentFormMixin, forms.ModelForm):
    class Meta:
        model = PlatformMailTemplate
        fields = ["name", "subject", "heading", "body_html", "cta_label", "cta_url", "active"]
        widgets = {
            "body_html": forms.Textarea(attrs={"rows": 14, "data-mail-html-source": ""}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", CLS)
        self.fields["name"].help_text = "A private name used to find this reusable topic."
        self.fields["subject"].help_text = self.personalization_help
        self.fields["heading"].help_text = self.personalization_help
        self.fields["body_html"].help_text = (
            "Format the message in the editor. Scripts, embedded content and unsafe links are removed. "
            + self.personalization_help
        )
        self.fields["cta_label"].label = "Button label"
        self.fields["cta_url"].label = "Button URL"
        self.fields["active"].help_text = "Only active topics can be loaded into a new campaign."


class PlatformMailComposeForm(PlatformMailContentFormMixin, forms.Form):
    template = forms.ModelChoiceField(
        queryset=PlatformMailTemplate.objects.none(),
        required=False,
        empty_label="Custom message (no saved topic)",
        label="Start from topic",
    )
    subject = forms.CharField(max_length=180, help_text=PlatformMailContentFormMixin.personalization_help)
    heading = forms.CharField(max_length=180, help_text=PlatformMailContentFormMixin.personalization_help)
    body_html = forms.CharField(
        label="Message",
        help_text=(
            "Format the message in the editor. Scripts, embedded content and unsafe links are removed. "
            + PlatformMailContentFormMixin.personalization_help
        ),
        widget=forms.Textarea(attrs={"rows": 14, "data-mail-html-source": ""}),
    )
    cta_label = forms.CharField(max_length=80, required=False, label="Button label")
    cta_url = forms.URLField(required=False, label="Button URL")
    business_ids = forms.CharField(required=True, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = PlatformMailTemplate.objects.filter(active=True).order_by("name")
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", CLS)

    def clean_business_ids(self):
        raw = (self.cleaned_data.get("business_ids") or "").strip()
        values = [value.strip() for value in raw.split(",") if value.strip()]
        if not values:
            raise forms.ValidationError("Choose at least one business recipient.")
        if len(values) > 10000 or any(not value.isdigit() for value in values):
            raise forms.ValidationError("The selected recipient list is invalid. Refresh and try again.")
        return tuple(dict.fromkeys(int(value) for value in values))
