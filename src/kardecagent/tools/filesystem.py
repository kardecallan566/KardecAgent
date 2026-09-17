from pathlib import Path
class ProjectFilesystem:
    def __init__(self,root:Path)->None:self.root=root.resolve()
    def safe_path(self,relative_path:str)->Path:
        candidate=(self.root/relative_path).resolve()
        try:candidate.relative_to(self.root)
        except ValueError as exc:raise ValueError("Path escapes the configured project root.") from exc
        return candidate
    def read_file(self,relative_path:str)->str:
        path=self.safe_path(relative_path)
        if not path.is_file():raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8")
    def write_file(self,relative_path:str,content:str)->None:
        path=self.safe_path(relative_path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding="utf-8")
    def list_files(self,limit:int=500)->list[str]:
        from ..project.context import project_snapshot
        return project_snapshot(self.root,limit)
