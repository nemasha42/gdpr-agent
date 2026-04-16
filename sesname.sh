for dir in .claude/worktrees/bridge-cse_*; do
  echo "=== $dir ==="
  git -C "$dir" log --oneline -3
  echo ""
done
