from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'


def test_backup_is_always_unique_and_not_hourly_reused():
    text = SCRIPT.read_text()
    assert 'BACKUP_REUSED' not in text
    assert 'named-config-$stamp-' in text
    assert 'od -An -N4' in text


def test_retention_is_thirty_days_not_five_archives():
    text = SCRIPT.read_text()
    assert "date -u -d '30 days ago' +%s" in text
    assert 'epoch < cutoff' in text
    assert '"$item.sha256"' in text
    assert '"${sorted[@]:5}"' not in text
