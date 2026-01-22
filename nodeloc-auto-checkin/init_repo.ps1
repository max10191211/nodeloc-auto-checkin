param(
  [Parameter(Mandatory=$true)]
  [string]$RepoUrl
)
git init
git add .
git commit -m "feat: init NodeLoc auto check-in repo"
git branch -M main
git remote add origin $RepoUrl
git push -u origin main
