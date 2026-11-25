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
        path=self.safe_path(relative_path)
        if path.exists() and not path.is_file():raise IsADirectoryError(relative_path)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(content,encoding="utf-8",newline="")
    def create_directory(self,relative_path:str)->None:
        path=self.safe_path(relative_path)
        if path.exists() and not path.is_dir():raise NotADirectoryError(relative_path)
        path.mkdir(parents=True,exist_ok=True)
    def append_file(self,relative_path:str,content:str)->None:
        path=self.safe_path(relative_path)
        if path.exists() and not path.is_file():raise IsADirectoryError(relative_path)
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("a",encoding="utf-8",newline="") as stream:stream.write(content)
    def replace_in_file(self,relative_path:str,find:str,replacement:str,replace_all:bool=False)->int:
        if not find:raise ValueError("find must not be empty")
        path=self.safe_path(relative_path)
        if not path.is_file():raise FileNotFoundError(relative_path)
        content=path.read_text(encoding="utf-8")
        count=content.count(find)
        if count == 0:return 0
        updated=content.replace(find,replacement) if replace_all else content.replace(find,replacement,1)
        self.write_file(relative_path,updated)
        return count if replace_all else 1
    def list_files(self,limit:int=500,relative_path:str=".")->list[str]:
        from ..project.context import iter_project_files
        base=self.safe_path(relative_path)
        if not base.is_dir():
            raise NotADirectoryError(relative_path)
        files=[]
        for path in iter_project_files(self.root):
            try:
                path.relative_to(base)
            except ValueError:
                continue
            files.append(path.relative_to(self.root).as_posix())
        return sorted(files)[:limit]
