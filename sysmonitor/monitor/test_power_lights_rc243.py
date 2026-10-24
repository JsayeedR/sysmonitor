"""Indicator behavior for missing/stale/status data; no live pings."""
from types import SimpleNamespace
from django.test import SimpleTestCase
from .power_lights import indicators

class PowerLightsTests(SimpleTestCase):
    def test_unknown_never_invented_as_normal(self):
        row=indicators([],None)
        self.assertEqual(row['pdb'],'UNKNOWN')
        self.assertEqual(row['equipment'],'UNKNOWN')
        self.assertEqual(row['generator'],'OFF')

    def test_recorded_statuses_are_authoritative(self):
        rows=[{'ip':'192.168.30.56','name':'PDB Holder','status':'UP'},
              {'ip':'192.168.30.80','name':'Main NVR','status':'DOWN'}]
        lights=indicators(rows,SimpleNamespace(gen_start='start',cycle_end=None))
        self.assertEqual((lights['pdb'],lights['generator'],lights['equipment']),('UP','ON','DOWN'))

    def test_completed_cycle_no_running_generator(self):
        lights=indicators([],SimpleNamespace(gen_start='start',cycle_end='end'))
        self.assertEqual(lights['generator'],'OFF')
