from types import SimpleNamespace
from datetime import date
from app.models.habit import Frequency
from app.utils.helpers import scheduled_on

def test_daily_schedule():
    h=SimpleNamespace(active=True,archived=False,start_date=date(2026,1,1),end_date=None,frequency=Frequency.DAILY,target_days=[])
    assert scheduled_on(h,date(2026,1,4))
