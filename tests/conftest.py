import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "modules" / "occasion" / "lambda"))

FIXTURES = REPO / "fixtures"
EXAMPLE = REPO / "src" / "partyplanner" / "example"
