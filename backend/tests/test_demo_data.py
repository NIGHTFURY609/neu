from app.demo_data import DemoData
from app.schemas import ReviewStatus


def test_demo_data_exposes_explainable_fixture_flow():
    data = DemoData()

    assert len(data.list_facts("DOC-001")) > 0
    assert len(data.list_edges("DOC-001", ReviewStatus.CONFIRMED)) > 0
    assert len(data.list_redlines("DOC-001", ReviewStatus.CONFIRMED)) == 1

    item = data.resolve_escalation(
        "ESC-RL-DOC-001-002",
        status=ReviewStatus.CONFIRMED,
        reviewer_id="demo-reviewer",
        edge_type=None,
    )

    assert item.status is ReviewStatus.CONFIRMED
    assert item.reviewer_id == "demo-reviewer"
    assert data.get_redline("RL-DOC-001-002").status is ReviewStatus.CONFIRMED
