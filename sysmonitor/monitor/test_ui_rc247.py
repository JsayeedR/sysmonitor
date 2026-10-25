"""RC24.7 dashboard layout / PDB date visibility checks; no database changes."""
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class DashboardRC247Tests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = (Path(settings.BASE_DIR) / 'monitor/templates/monitor/dashboard.html').read_text()

    def test_six_cards_remain_in_their_expected_grid_cells(self):
        self.assertIn('grid-template-areas:"status lamps cycle" "ats clock colo"', self.html)
        self.assertIn('grid-auto-rows:120px;', self.html)
        self.assertIn('.banner-grid > .banner { box-sizing:border-box;', self.html)
        self.assertIn('grid-auto-rows:minmax(120px, auto)', self.html)

    def test_limit_to_latest_35_without_server_filtering(self):
        self.assertIn('const PDB_INITIAL_DATES = 35;', self.html)
        self.assertIn('.sort((a, b) => b.localeCompare(a))', self.html)
        self.assertIn('index >= PDB_INITIAL_DATES', self.html)
        self.assertIn('data-pdb-day="1"', self.html)

    def test_toggle_expand_collapse_responsive_rows(self):
        self.assertIn('function togglePdbOutageDates()', self.html)
        self.assertIn('button.hidden = !pdbDatesExpanded', self.html)
        self.assertIn('Show all dates', self.html)
        self.assertIn('Show latest 35', self.html)
        self.assertIn('aria-expanded', self.html)
        self.assertIn('display: flex; flex-wrap: wrap;', self.html)

    def test_operational_data_logic_untouched(self):
        self.assertIn('function loadSummary(dateVal)', self.html)
        self.assertIn("fetch('{{ base_path }}/api/daily-summary/'", self.html)
        self.assertIn('id="sm-clock-second"', self.html)
        self.assertIn('id="sm-pdb-light"', self.html)
