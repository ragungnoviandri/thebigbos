# Handling Large Files in Git History

GitHub rejects pushes containing files > 100 MB in **any commit** in the history. Even if the file was deleted in a later commit, it still blocks the push.

## Detection

```bash
# Find large files in current commit
git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '/^blob/ {print $4 " " $3}' | sort -rn -k2 | head -20

# Find which commit introduced a specific large file
git log --all --diff-filter=A -- '*.sql'
git log --all --diff-filter=A -- '*.bak'
git log --all --diff-filter=A -- '*.zip'
```

## Prevention: .gitignore

```gitignore
# Add BEFORE committing large files
*.sql
*.sql.gz
*.bak
*.zip
*.tar.gz
*.log
node_modules/
*.csv
*.tsv
*.parquet
```

## Resolution: Remove from History

### Option A: git filter-branch (universal, works everywhere)

```bash
# Remove specific files from ALL commits
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch 'path/to/large-file.sql' 'another-large.zip'" \
  --prune-empty --tag-name-filter cat -- --all

# Clean up backup refs saved by filter-branch
git for-each-ref --format='%(refname)' refs/original/ | \
  while read ref; do git update-ref -d "$ref"; done

# Expire reflog and garbage collect
git reflog expire --expire=now --all
git gc --aggressive --prune=now
```

⚠️ **filter-branch caveats:** rewrites ALL affected commits. If collaborators have the old history, they'll need to `git pull --rebase` or re-clone. Works on Windows git-bash as well as Linux/macOS.

### Option B: git filter-repo (faster, Python-based, recommended)

```bash
# Install (one-time)
pip install git-filter-repo

# Remove files
git filter-repo --path 'path/to/large-file.sql' --invert-paths

# Then force push
git push origin --force --all
```

### Option C: BFG Repo-Cleaner (fast, Java-based)

```bash
# Download BFG jar and run
java -jar bfg.jar --delete-files '*.sql' .git
git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

## After Rewrite: Force Push

⚠️ **Requires --force** since history changed:

```bash
# Double-check what you're about to push
git log --oneline origin/master..HEAD

# Force push
git push origin --force
# OR (safer — checks remote matches expectation)
git push --force-with-lease origin master
```

If collaborators:
```bash
# They need to re-base or re-clone
git fetch origin
git checkout master
git reset --hard origin/master
```

## Alternative: Git LFS (keep large files tracked)

If the file is needed in the repo but too large:

```bash
# Install LFS
git lfs install

# Track file type
git lfs track '*.sql'

# Add .gitattributes
git add .gitattributes
git commit -m "chore: track *.sql with Git LFS"
```

LFS stores a pointer in the repo and the actual file on GitHub's LFS storage. Requires LFS quota on the GitHub plan.

## Checklist for Push Failure Recovery

1. [ ] Read the full error — note exact file name and size
2. [ ] Check if file is still on disk or only in history
3. [ ] Add the file pattern to `.gitignore` (prevent re-occurrence)
4. [ ] Commit `.gitignore` change
5. [ ] Remove file from ALL history with filter-branch/filter-repo
6. [ ] Clean up backup refs and gc
7. [ ] Force push (with `--force-with-lease` for safety)
8. [ ] Notify collaborators to re-base
