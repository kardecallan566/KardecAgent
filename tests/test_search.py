from pathlib import Path
from kardecagent.tools.search import search_text
def test_search(tmp_path:Path):
    (tmp_path/"example.py").write_text("hello\nworld\n",encoding="utf-8");r=search_text(tmp_path,"WORLD");assert r[0]["line"]==2
