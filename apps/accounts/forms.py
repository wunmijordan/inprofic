from django import forms
from django.contrib.auth import password_validation
from core.models import Business
from .models import CustomUser, Role, RoleModulePermission, UserBusiness, UserModulePermission, SubscriptionPlan, SubscriptionPromotion, MarketingPromoCampaign
from .services import ensure_permissions, is_business_admin, seed_business_roles

CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class BusinessSignupForm(forms.Form):
    business_name = forms.CharField(max_length=120, label="Business name")
    vertical = forms.ChoiceField(label="Service", choices=Business.VERTICAL_CHOICES)
    fullname = forms.CharField(max_length=160, label="Your full name")
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
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account already uses this email. Sign in instead.")
        return email

    def clean(self):
        cleaned = super().clean()
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
    commerce_storefront_access = forms.BooleanField(
        required=False,
        label="Commerce storefront access",
        help_text="Allow this staff member to operate the in-premise storefront/POS without granting full Commerce administration rights.",
    )

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
        self.fields["commerce_storefront_access"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"
        if membership:
            self.fields["role"].initial = membership.role_id
            self.fields["commerce_storefront_access"].initial = membership.commerce_storefront_access

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


class PermissionMatrixForm(forms.Form):
    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.membership = membership
        ensure_permissions(membership)
        for module, label in RoleModulePermission.MODULE_CHOICES:
            p = membership.module_permissions.get(module=module)
            role_p = membership.role.module_permissions.filter(module=module).first()
            self.fields[f"{module}_view"] = forms.BooleanField(required=False, label=f"{label}: View", initial=p.can_view if p.can_view is not None else (role_p.can_view if role_p else False))
            self.fields[f"{module}_edit"] = forms.BooleanField(required=False, label=f"{label}: Edit", initial=p.can_edit if p.can_edit is not None else (role_p.can_edit if role_p else False))
            self.fields[f"{module}_view"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"
            self.fields[f"{module}_edit"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"

    def save(self):
        for module, _ in RoleModulePermission.MODULE_CHOICES:
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
        fields = ["plan", "reason", "discount_type", "discount_value", "starts_at", "ends_at"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id")
        for field in self.fields.values():
            field.widget.attrs["class"] = CLS

    def clean(self):
        from decimal import Decimal
        cleaned = super().clean()
        plan = cleaned.get("plan")
        kind = cleaned.get("discount_type")
        value = cleaned.get("discount_value")
        starts = cleaned.get("starts_at")
        ends = cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", "Promotion expiry must be after its start time.")
        if value is not None and value <= 0:
            self.add_error("discount_value", "Enter a discount greater than zero.")
        if kind == SubscriptionPromotion.DISCOUNT_PERCENT and value is not None and value >= Decimal("100"):
            self.add_error("discount_value", "Percentage discounts must leave a payable amount (use less than 100%).")
        if kind == SubscriptionPromotion.DISCOUNT_AMOUNT and plan and value is not None and value >= plan.monthly_price:
            self.add_error("discount_value", "A fixed discount must leave a payable amount below the plan's monthly base price.")
        if plan and starts and ends:
            overlap = SubscriptionPromotion.objects.filter(
                plan=plan, active=True, starts_at__lt=ends, ends_at__gt=starts
            )
            if self.instance.pk:
                overlap = overlap.exclude(pk=self.instance.pk)
            if overlap.exists():
                self.add_error(None, "This plan already has an active/scheduled promotion overlapping that period.")
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
        self.fields["promotion"].label_from_instance = lambda obj: f"{obj.plan.name} — {obj.reason}"
        self.fields["content_html"].required = True
        self.fields["content_html"].help_text = "Use the editor below. Only INPROFIC's bundled fonts and safe text formatting are retained."
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


class LegacyTenantImportForm(forms.Form):
    target_business = forms.ModelChoiceField(
        queryset=Business.objects.none(),
        label="Destination tenant",
        help_text="Choose the existing Render/Supabase tenant that should receive this legacy data.",
        widget=forms.Select(attrs={"class": CLS}),
    )
    database = forms.FileField(
        label="Legacy tenant backup",
        help_text="Upload an INPROFIC JSON backup or the original PythonAnywhere .sqlite3/.db file. The first pass is read-only.",
        widget=forms.ClearableFileInput(attrs={"accept": ".json,.sqlite,.sqlite3,.db,application/json,application/vnd.sqlite3,application/octet-stream", "class": CLS}),
    )
    source_business_id = forms.IntegerField(
        required=False,
        min_value=1,
        label="Legacy tenant ID",
        help_text="Leave blank if the backup contains one tenant. If it contains several, Dry run will list their IDs.",
        widget=forms.NumberInput(attrs={"class": CLS, "placeholder": "Auto-detect when possible"}),
    )
    confirmation = forms.CharField(
        required=False,
        label="Import confirmation",
        help_text="Required only for the real import. Type IMPORT followed by the destination tenant slug, e.g. IMPORT my-business.",
        widget=forms.TextInput(attrs={"class": CLS, "autocomplete": "off"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_business"].queryset = Business.objects.order_by("name", "id")

    def clean_database(self):
        uploaded = self.cleaned_data["database"]
        if getattr(uploaded, "size", 0) > 200 * 1024 * 1024:
            raise forms.ValidationError("Use a legacy backup no larger than 200 MB in the Founder Console importer.")
        return uploaded
