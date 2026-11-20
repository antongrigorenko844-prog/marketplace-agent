"""Persist the designer's receipts and files together without replacing parallel edits."""
import os
from pathlib import Path
import subprocess
import tempfile


# Do not stage catalogues, marketplace data or another job's output.
OWNED = ("data/designer_card*", "data/designer_state.json", "data/designer_returns",
         "data/designer_chat.json", "data/owner_chat.json", "data/outbox_done.json",
         "data/post_texts", "data/social_hold.txt", "data/alert_state/designer-*.txt",
         "data/alert_state/owner-preview.txt", "social")
GUARD = ("designer_bot.py", "designer_card*.py", "designer_checkpoint.py",
         "data/designer_card*", "data/designer_state.json", "data/designer_chat.json",
         "data/owner_chat.json", "data/outbox_done.json")
IDENTITY = ("-c", "user.name=marketplace-agent-bot", "-c", "user.email=actions@users.noreply.github.com")
MESSAGE = "chore: save designer work [skip ci]"


class CheckpointError(RuntimeError):
    pass


def git(root, *args, env=None, check=True):
    result = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True)
    if check and result.returncode:
        raise CheckpointError("Не удалось подтвердить сохранение в Git.") from None
    return result


def files(output):
    return set(os.fsdecode(name) for name in output.split(b"\0") if name)


def save(root):
    changed = files(git(root, "diff", "--name-only", "-z", "HEAD", "--", *OWNED).stdout)
    changed.update(files(git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", *OWNED).stdout))
    # A new poll may follow an unrelated writer. Compare even when nothing is dirty
    # so that a stale offset or a stale chat binding cannot authorize a delivery.
    with tempfile.TemporaryDirectory() as folder:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(folder) / "index"), GIT_LITERAL_PATHSPECS="1")
        paths = sorted(changed)
        for _ in range(4):
            git(root, "fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
            remote = git(root, "rev-parse", "FETCH_HEAD").stdout.decode().strip()
            upstream = files(git(root, "diff", "--name-only", "-z", "HEAD", remote).stdout)
            moved = git(root, "diff", "--quiet", "HEAD", remote, "--", *GUARD, check=False)
            if moved.returncode or upstream & changed:
                raise CheckpointError("Данные дизайнера изменились в main. Повторите со свежей версии.")
            if not changed:
                return
            git(root, "read-tree", remote, env=env)
            git(root, "add", "-A", "--", *paths, env=env)
            tree = git(root, "write-tree", env=env).stdout.decode().strip()
            new = git(root, *IDENTITY, "commit-tree", tree, "-p", remote, "-m", MESSAGE).stdout.decode().strip()
            if git(root, "push", "--quiet", "origin", new + ":refs/heads/main", check=False).returncode:
                continue
            # Keep the local baseline for subsequent receipts without checking out
            # unrelated upstream files under the still-running Python process.
            local_env = dict(os.environ, GIT_LITERAL_PATHSPECS="1")
            git(root, "add", "-A", "--", *paths, env=local_env)
            git(root, *IDENTITY, "commit", "-q", "--only", "-m", MESSAGE, "--", *paths, env=local_env)
            return
    raise CheckpointError("Git не подтвердил сохранение после четырёх попыток.")


def checkpoint():
    if os.environ.get("DESIGNER_PERSIST") != "1":
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise CheckpointError("Сохранение разрешено только в рабочем процессе дизайнера.")
        return
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise CheckpointError("Сохранение доступно только в main.")
    save(Path(__file__).resolve().parent)


if __name__ == "__main__":
    checkpoint()
