from django.test import SimpleTestCase

from .share_text import bullet, render_lines


class ShareTextTests(SimpleTestCase):
    def test_main_and_nested_items_have_distinct_markers(self):
        self.assertEqual(render_lines(['*Heading*', '', '• Customer', '  • Product', '  ◦ Amount']),
            '*Heading*\n\n• Customer\n  ◦ Product\n  ◦ Amount')
        self.assertEqual(bullet('  Product: 2 loaves', nested=True), '  ◦ Product: 2 loaves')
