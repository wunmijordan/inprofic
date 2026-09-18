"""Small, versioned onboarding surface for ordinary multi-module users.

The visual tour is rendered client-side from template step data so it adds no
extra dashboard queries. Increment CURRENT_TOUR_VERSION only when an important
new tour should be shown again to previously completed memberships.
"""

CURRENT_TOUR_VERSION = 1
