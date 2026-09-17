from pathlib import Path
from ..project.context import iter_project_files
def search_text(root:Path,query:str,max_results:int=50)->list[dict[str,str|int]]:
    results=[]
    for path in iter_project_files(root):
        try:text=path.read_text(encoding="utf-8")
        except (UnicodeDecodeError,OSError):continue
        for line_number,line in enumerate(text.splitlines(),1):
            if query.lower() in line.lower():
                results.append({"path":path.relative_to(root).as_posix(),"line":line_number,"text":line.strip()})
                if len(results)>=max_results:return results
    return results
