from django.contrib import admin
from django.test import RequestFactory, SimpleTestCase

from .models import CustomUser


class CustomUserAdminTests(SimpleTestCase):
    def test_change_form_keeps_non_editable_dates_read_only(self):
        request = RequestFactory().get("/admin/accounts/customuser/5/change/")
        request.user = CustomUser(
            username="founder",
            fullname="Founder",
            is_staff=True,
            is_superuser=True,
        )
        registered_admin = admin.site._registry[CustomUser]

        form = registered_admin.get_form(request, CustomUser())

        self.assertEqual(
            registered_admin.readonly_fields,
            ("last_login", "date_joined"),
        )
        self.assertNotIn("date_joined", form.base_fields)
        self.assertNotIn("last_login", form.base_fields)
