import subprocess

from openlocalweather.cli import _github_repo_slug


def test_github_repo_slug_prefers_env_var(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "dissent00/open-local-weather")
    assert _github_repo_slug() == "dissent00/open-local-weather"


def test_github_repo_slug_falls_back_to_git_remote_https(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/dissent00/open-local-weather.git\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == "dissent00/open-local-weather"


def test_github_repo_slug_falls_back_to_git_remote_ssh(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="git@github.com:dissent00/open-local-weather.git\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == "dissent00/open-local-weather"


def test_github_repo_slug_returns_empty_on_non_github_remote(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://gitlab.com/someone/somewhere.git\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == ""


def test_github_repo_slug_returns_empty_when_git_fails(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == ""
