#!/bin/bash

# Script to create a git worktree and launch Claude Code
# Usage: ./claude-worktree.sh

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Claude Code Worktree Helper ===${NC}"
echo

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${YELLOW}Error: Not in a git repository${NC}"
    exit 1
fi

# Get the repository name and root
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")
REPO_ROOT=$(git rev-parse --show-toplevel)

# Function to list existing branches
list_branches() {
    echo -e "${GREEN}Existing branches:${NC}"
    git branch -a | sed 's/remotes\/origin\///' | sort | uniq | nl
}

# Function to parse open issues from TODO.md
parse_open_issues() {
    local todo_file="$REPO_ROOT/TODO.md"
    if [[ ! -f "$todo_file" ]]; then
        return 1
    fi
    
    # Parse the simple checkbox format: - [ ] branch-name: description [Priority]
    grep -E "^- \[ \]" "$todo_file" | while read -r line; do
        # Extract branch name, description, and priority
        if [[ "$line" =~ ^-\ \[\ \]\ ([^:]+):\ (.+)\ \[([^\]]+)\]$ ]]; then
            branch_name="${BASH_REMATCH[1]}"
            description="${BASH_REMATCH[2]}"
            priority="${BASH_REMATCH[3]}"
            echo "${branch_name}|${description}|${priority}"
        fi
    done
}

# Function to convert issue name to branch name (no longer needed as branch names are pre-defined)
issue_to_branch_name() {
    echo "$1"
}

# Function to update issue status in TODO.md
update_issue_status() {
    local branch_name="$1"
    local todo_file="$REPO_ROOT/TODO.md"
    
    if [[ ! -f "$todo_file" ]]; then
        return 1
    fi
    
    # Update checkbox from [ ] to [x] for the given branch
    sed -i "s/^- \[ \] ${branch_name}:/- [x] ${branch_name}:/" "$todo_file"
    
    echo -e "${GREEN}Updated TODO.md: Marked '${branch_name}' as complete${NC}"
}

# Ask user what they want to do
echo "What would you like to do?"
echo "1) Create a worktree for an existing branch"
echo "2) Create a worktree with a new branch"
echo "3) Create a worktree for a TODO.md issue"
echo "4) List current worktrees"
read -p "Choose an option (1-4): " choice

case $choice in
    1)
        # List branches and let user choose
        list_branches
        echo
        read -p "Enter the branch name (or number from list): " branch_input
        
        # Check if input is a number
        if [[ "$branch_input" =~ ^[0-9]+$ ]]; then
            branch=$(git branch -a | sed 's/remotes\/origin\///' | sort | uniq | sed -n "${branch_input}p" | sed 's/^[* ]*//')
        else
            branch=$branch_input
        fi
        
        # Validate branch exists
        if ! git show-ref --verify --quiet "refs/heads/$branch" && ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
            echo -e "${YELLOW}Error: Branch '$branch' not found${NC}"
            exit 1
        fi
        
        # Create directory name
        dir_name="../${REPO_NAME}-${branch//\//-}"
        
        # Create worktree
        echo -e "${GREEN}Creating worktree for branch '$branch' in '$dir_name'...${NC}"
        git worktree add "$dir_name" "$branch"
        ;;
        
    2)
        # Create new branch
        read -p "Enter the new branch name: " new_branch
        
        # Ask for base branch
        echo
        echo "Which branch should this be based on?"
        list_branches
        echo
        read -p "Enter the base branch name (default: main): " base_branch
        base_branch=${base_branch:-main}
        
        # Validate base branch exists
        if ! git show-ref --verify --quiet "refs/heads/$base_branch" && ! git show-ref --verify --quiet "refs/remotes/origin/$base_branch"; then
            echo -e "${YELLOW}Error: Branch '$base_branch' not found${NC}"
            exit 1
        fi
        
        # Create directory name
        dir_name="../${REPO_NAME}-${new_branch//\//-}"
        
        # Create worktree with new branch
        echo -e "${GREEN}Creating new branch '$new_branch' based on '$base_branch' in '$dir_name'...${NC}"
        git worktree add -b "$new_branch" "$dir_name" "$base_branch"
        branch=$new_branch
        ;;
        
    3)
        # Create worktree for TODO.md issue
        echo -e "${CYAN}Checking for open issues in TODO.md...${NC}"
        
        # Check if TODO.md exists
        if [[ ! -f "$REPO_ROOT/TODO.md" ]]; then
            echo -e "${YELLOW}No TODO.md file found in repository root${NC}"
            exit 1
        fi
        
        # Get open issues
        mapfile -t issues < <(parse_open_issues)
        
        if [[ ${#issues[@]} -eq 0 ]]; then
            echo -e "${YELLOW}No open issues found in TODO.md${NC}"
            exit 1
        fi
        
        # Display open issues
        echo -e "${GREEN}Open issues:${NC}"
        echo
        for i in "${!issues[@]}"; do
            IFS='|' read -r branch_name description priority <<< "${issues[$i]}"
            printf "%2d) ${CYAN}%-25s${NC} - %s ${YELLOW}[%s]${NC}\n" $((i+1)) "$branch_name" "$description" "$priority"
        done
        
        echo
        read -p "Select an issue number (or 0 to cancel): " issue_num
        
        if [[ "$issue_num" == "0" ]]; then
            echo "Cancelled"
            exit 0
        fi
        
        if [[ ! "$issue_num" =~ ^[0-9]+$ ]] || [[ "$issue_num" -lt 1 ]] || [[ "$issue_num" -gt ${#issues[@]} ]]; then
            echo -e "${YELLOW}Invalid selection${NC}"
            exit 1
        fi
        
        # Get selected issue
        selected_issue="${issues[$((issue_num-1))]}"
        IFS='|' read -r branch_name description priority <<< "$selected_issue"
        
        echo
        echo -e "${GREEN}Selected issue:${NC} $description"
        echo -e "${GREEN}Branch name:${NC} $branch_name"
        
        new_branch="$branch_name"
        
        # Ask for base branch
        echo
        echo "Which branch should this be based on?"
        list_branches
        echo
        read -p "Enter the base branch name (default: main): " base_branch
        base_branch=${base_branch:-main}
        
        # Validate base branch exists
        if ! git show-ref --verify --quiet "refs/heads/$base_branch" && ! git show-ref --verify --quiet "refs/remotes/origin/$base_branch"; then
            echo -e "${YELLOW}Error: Branch '$base_branch' not found${NC}"
            exit 1
        fi
        
        # Create directory name
        dir_name="../${REPO_NAME}-${new_branch//\//-}"
        
        # Create worktree with new branch
        echo -e "${GREEN}Creating new branch '$new_branch' based on '$base_branch' in '$dir_name'...${NC}"
        git worktree add -b "$new_branch" "$dir_name" "$base_branch"
        branch=$new_branch
        
        # Ask if user wants to update TODO.md
        echo
        read -p "Mark this issue as complete in TODO.md? (y/n): " update_todo
        
        if [[ "$update_todo" =~ ^[Yy]$ ]]; then
            update_issue_status "$branch_name"
        fi
        ;;
        
    4)
        # List worktrees
        echo -e "${GREEN}Current worktrees:${NC}"
        git worktree list
        exit 0
        ;;
        
    *)
        echo -e "${YELLOW}Invalid option${NC}"
        exit 1
        ;;
esac

# Ask if user wants to launch Claude
echo
read -p "Launch Claude Code in the new worktree? (y/n): " launch_claude

if [[ "$launch_claude" =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}Launching Claude Code...${NC}"
    cd "$dir_name"
    claude
else
    echo -e "${BLUE}Worktree created successfully!${NC}"
    echo -e "To use it: ${GREEN}cd $dir_name && claude${NC}"
fi