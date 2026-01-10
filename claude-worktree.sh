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

# Base ports for the project (matches main worktree .env)
BASE_BACKEND_PORT=5000
BASE_FRONTEND_PORT=5174
BASE_REDIS_PORT=6379
BASE_METABASE_PORT=3002

echo -e "${BLUE}=== Claude Code Worktree Helper ===${NC}"
echo

# Function to detect the next available port offset by scanning running containers
detect_next_port_offset() {
    local max_offset=0

    # Helper to update max_offset
    update_max() {
        local offset=$1
        if [[ $offset -ge 0 && $offset -gt $max_offset ]]; then
            max_offset=$offset
        fi
    }

    if command -v docker &> /dev/null; then
        local all_ports
        all_ports=$(docker ps --format '{{.Ports}}' 2>/dev/null)

        # Scan backend ports (5000-5099)
        while read -r port; do
            [[ -n "$port" && "$port" =~ ^[0-9]+$ ]] && update_max $((port - BASE_BACKEND_PORT))
        done < <(echo "$all_ports" | grep -oE "[0-9.]*:50[0-9]{2}" | grep -oE "50[0-9]{2}" | sort -u)

        # Scan frontend ports (5173-5199)
        while read -r port; do
            [[ -n "$port" && "$port" =~ ^[0-9]+$ ]] && update_max $((port - BASE_FRONTEND_PORT))
        done < <(echo "$all_ports" | grep -oE "[0-9.]*:51[0-9]{2}" | grep -oE "51[0-9]{2}" | sort -u)

        # Scan redis ports (6379-6399)
        while read -r port; do
            [[ -n "$port" && "$port" =~ ^[0-9]+$ ]] && update_max $((port - BASE_REDIS_PORT))
        done < <(echo "$all_ports" | grep -oE "[0-9.]*:63[0-9]{2}" | grep -oE "63[0-9]{2}" | sort -u)

        # Scan metabase ports (3002-3099)
        while read -r port; do
            [[ -n "$port" && "$port" =~ ^[0-9]+$ ]] && update_max $((port - BASE_METABASE_PORT))
        done < <(echo "$all_ports" | grep -oE "[0-9.]*:30[0-9]{2}" | grep -oE "30[0-9]{2}" | sort -u)
    fi

    # Also check for existing .env files in sibling worktree directories
    for env_file in ../my-poker-face*/.env; do
        if [[ -f "$env_file" ]]; then
            local backend_port
            backend_port=$(grep "^BACKEND_PORT=" "$env_file" 2>/dev/null | cut -d= -f2)
            if [[ -n "$backend_port" && "$backend_port" =~ ^[0-9]+$ ]]; then
                update_max $((backend_port - BASE_BACKEND_PORT))
            fi
        fi
    done

    echo $((max_offset + 1))
}

# Function to setup .env file with auto-assigned ports
setup_env_file() {
    local worktree_dir="$1"
    local port_offset="$2"

    local source_env="$REPO_ROOT/.env.example"
    local target_env="$worktree_dir/.env"

    if [[ ! -f "$source_env" ]]; then
        echo -e "${YELLOW}Warning: No .env.example found, skipping env setup${NC}"
        return 1
    fi

    # Copy template
    cp "$source_env" "$target_env"

    # Calculate ports
    local backend_port=$((BASE_BACKEND_PORT + port_offset))
    local frontend_port=$((BASE_FRONTEND_PORT + port_offset))
    local redis_port=$((BASE_REDIS_PORT + port_offset))
    local metabase_port=$((BASE_METABASE_PORT + port_offset))

    # Update ports in .env
    sed -i "s/^BACKEND_PORT=.*/BACKEND_PORT=$backend_port/" "$target_env"
    sed -i "s/^FRONTEND_PORT=.*/FRONTEND_PORT=$frontend_port/" "$target_env"
    sed -i "s/^REDIS_PORT=.*/REDIS_PORT=$redis_port/" "$target_env"

    # Add METABASE_PORT if not present
    if grep -q "^METABASE_PORT=" "$target_env"; then
        sed -i "s/^METABASE_PORT=.*/METABASE_PORT=$metabase_port/" "$target_env"
    else
        echo "METABASE_PORT=$metabase_port" >> "$target_env"
    fi

    # Copy API key from main .env if it exists
    if [[ -f "$REPO_ROOT/.env" ]]; then
        local api_key
        api_key=$(grep "^OPENAI_API_KEY=" "$REPO_ROOT/.env" 2>/dev/null | cut -d= -f2)
        if [[ -n "$api_key" && "$api_key" != "your_openai_api_key_here" ]]; then
            sed -i "s/^OPENAI_API_KEY=.*/OPENAI_API_KEY=$api_key/" "$target_env"
        fi
    fi

    # Generate secret keys
    local secret_key
    local jwt_secret
    secret_key=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    jwt_secret=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)

    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$secret_key/" "$target_env"
    sed -i "s/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=$jwt_secret/" "$target_env"

    echo -e "${GREEN}Created .env with ports:${NC}"
    echo -e "  Backend:  ${CYAN}$backend_port${NC}"
    echo -e "  Frontend: ${CYAN}$frontend_port${NC}"
    echo -e "  Redis:    ${CYAN}$redis_port${NC}"
    echo -e "  Metabase: ${CYAN}$metabase_port${NC}"

    return 0
}

# Function to create tmux session with standard windows
create_tmux_session() {
    local worktree_dir="$1"
    local session_name="$2"

    # Check if tmux is available
    if ! command -v tmux &> /dev/null; then
        echo -e "${YELLOW}tmux not found, skipping session creation${NC}"
        return 1
    fi

    # Check if session already exists
    if tmux has-session -t "$session_name" 2>/dev/null; then
        echo -e "${YELLOW}tmux session '$session_name' already exists${NC}"
        read -p "Attach to existing session? (y/n): " attach_existing
        if [[ "$attach_existing" =~ ^[Yy]$ ]]; then
            tmux attach -t "$session_name"
        fi
        return 0
    fi

    local abs_worktree_dir
    abs_worktree_dir=$(cd "$worktree_dir" && pwd)

    echo -e "${CYAN}Creating tmux session '$session_name'...${NC}"

    # Create new session with first window (claude)
    tmux new-session -d -s "$session_name" -n "claude" -c "$abs_worktree_dir"

    # Window 1: Claude - start claude code
    tmux send-keys -t "$session_name:claude" "claude" Enter

    # Window 2: Docker logs
    tmux new-window -t "$session_name" -n "docker" -c "$abs_worktree_dir"
    tmux send-keys -t "$session_name:docker" "docker compose logs -f" Enter

    # Window 3: Shell
    tmux new-window -t "$session_name" -n "shell" -c "$abs_worktree_dir"

    # Select the claude window
    tmux select-window -t "$session_name:claude"

    echo -e "${GREEN}tmux session created with windows:${NC}"
    echo -e "  1) ${CYAN}claude${NC} - Claude Code (running)"
    echo -e "  2) ${CYAN}docker${NC} - Docker compose logs"
    echo -e "  3) ${CYAN}shell${NC}  - General shell"

    return 0
}

# Function to get session name from worktree directory
get_session_name() {
    local dir_name="$1"
    # Convert to short session name (e.g., "poker-feature-x")
    echo "$dir_name" | sed 's/my-poker-face-/poker-/' | sed 's/my-poker-face/poker-main/'
}

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
echo "5) Clean up a worktree (stop Docker + tmux + remove)"
echo "6) Attach to a worktree tmux session"
read -p "Choose an option (1-6): " choice

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

    5)
        # Clean up a worktree
        echo -e "${GREEN}Current worktrees:${NC}"
        echo

        # Get worktrees (excluding the main one)
        mapfile -t worktrees < <(git worktree list --porcelain | grep "^worktree " | cut -d' ' -f2- | grep -v "^$REPO_ROOT$")

        if [[ ${#worktrees[@]} -eq 0 ]]; then
            echo -e "${YELLOW}No additional worktrees to clean up${NC}"
            exit 0
        fi

        # Display worktrees with numbers
        for i in "${!worktrees[@]}"; do
            wt_path="${worktrees[$i]}"
            wt_name=$(basename "$wt_path")

            # Check if Docker containers are running for this worktree
            container_count=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c "^${wt_name}" || echo "0")
            if [[ "$container_count" -gt 0 ]]; then
                docker_status="${CYAN}($container_count containers running)${NC}"
            else
                docker_status="${YELLOW}(no containers)${NC}"
            fi

            printf "%2d) %s %b\n" $((i+1)) "$wt_name" "$docker_status"
        done

        echo
        read -p "Select worktree to remove (or 0 to cancel): " wt_num

        if [[ "$wt_num" == "0" ]]; then
            echo "Cancelled"
            exit 0
        fi

        if [[ ! "$wt_num" =~ ^[0-9]+$ ]] || [[ "$wt_num" -lt 1 ]] || [[ "$wt_num" -gt ${#worktrees[@]} ]]; then
            echo -e "${YELLOW}Invalid selection${NC}"
            exit 1
        fi

        selected_wt="${worktrees[$((wt_num-1))]}"
        selected_name=$(basename "$selected_wt")

        echo
        echo -e "${YELLOW}Selected: $selected_name${NC}"
        echo -e "Path: $selected_wt"

        # Check for running containers
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${selected_name}"; then
            echo
            echo -e "${CYAN}Stopping Docker containers...${NC}"
            (cd "$selected_wt" && docker compose down 2>/dev/null) || true
            echo -e "${GREEN}Containers stopped${NC}"
        fi

        # Check for tmux session
        session_name=$(get_session_name "$selected_name")
        if tmux has-session -t "$session_name" 2>/dev/null; then
            echo
            echo -e "${CYAN}Killing tmux session '$session_name'...${NC}"
            tmux kill-session -t "$session_name" 2>/dev/null || true
            echo -e "${GREEN}tmux session killed${NC}"
        fi

        # Confirm removal
        echo
        read -p "Remove worktree '$selected_name'? This cannot be undone. (y/n): " confirm

        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            echo -e "${CYAN}Removing worktree...${NC}"
            git worktree remove "$selected_wt" --force
            echo -e "${GREEN}Worktree '$selected_name' removed successfully${NC}"

            # Ask about branch deletion
            branch_name=$(git branch --list | grep -F "$selected_name" | head -1 | sed 's/^[* ]*//')
            if [[ -n "$branch_name" ]]; then
                echo
                read -p "Also delete the branch '$branch_name'? (y/n): " delete_branch
                if [[ "$delete_branch" =~ ^[Yy]$ ]]; then
                    git branch -D "$branch_name" 2>/dev/null && \
                        echo -e "${GREEN}Branch '$branch_name' deleted${NC}" || \
                        echo -e "${YELLOW}Could not delete branch (may not exist or is current)${NC}"
                fi
            fi
        else
            echo "Cancelled"
        fi

        exit 0
        ;;

    6)
        # Attach to a worktree tmux session
        echo -e "${GREEN}Available tmux sessions:${NC}"
        echo

        # Get poker-related tmux sessions
        mapfile -t sessions < <(tmux list-sessions -F "#{session_name}" 2>/dev/null | grep "^poker-" || true)

        if [[ ${#sessions[@]} -eq 0 ]]; then
            echo -e "${YELLOW}No poker worktree sessions found${NC}"
            echo -e "Create a worktree first with options 1-3"
            exit 0
        fi

        # Display sessions with status
        for i in "${!sessions[@]}"; do
            sess="${sessions[$i]}"
            # Get window count and attached status
            window_count=$(tmux list-windows -t "$sess" 2>/dev/null | wc -l)
            if tmux list-clients -t "$sess" 2>/dev/null | grep -q .; then
                attached="${CYAN}(attached)${NC}"
            else
                attached="${YELLOW}(detached)${NC}"
            fi
            printf "%2d) %s - %d windows %b\n" $((i+1)) "$sess" "$window_count" "$attached"
        done

        echo
        read -p "Select session to attach (or 0 to cancel): " sess_num

        if [[ "$sess_num" == "0" ]]; then
            echo "Cancelled"
            exit 0
        fi

        if [[ ! "$sess_num" =~ ^[0-9]+$ ]] || [[ "$sess_num" -lt 1 ]] || [[ "$sess_num" -gt ${#sessions[@]} ]]; then
            echo -e "${YELLOW}Invalid selection${NC}"
            exit 1
        fi

        selected_session="${sessions[$((sess_num-1))]}"
        echo -e "${GREEN}Attaching to '$selected_session'...${NC}"
        tmux attach -t "$selected_session"
        exit 0
        ;;

    *)
        echo -e "${YELLOW}Invalid option${NC}"
        exit 1
        ;;
esac

# Setup environment for the new worktree
echo
echo -e "${BLUE}Setting up environment...${NC}"

# Detect next available port offset
port_offset=$(detect_next_port_offset)
echo -e "Detected next available port offset: ${CYAN}+$port_offset${NC}"

# Setup .env file
if setup_env_file "$dir_name" "$port_offset"; then
    echo -e "${GREEN}Environment configured successfully!${NC}"
else
    echo -e "${YELLOW}Could not auto-configure environment. You may need to copy .env manually.${NC}"
fi

# Ask if user wants to start Docker
echo
read -p "Start Docker containers for this worktree? (y/n): " start_docker

if [[ "$start_docker" =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}Starting Docker containers...${NC}"
    cd "$dir_name"
    docker compose up -d --build
    echo
    echo -e "${GREEN}Containers started! Access at:${NC}"
    backend_port=$((BASE_BACKEND_PORT + port_offset))
    frontend_port=$((BASE_FRONTEND_PORT + port_offset))
    echo -e "  Frontend: ${CYAN}http://localhost:$frontend_port${NC}"
    echo -e "  Backend:  ${CYAN}http://localhost:$backend_port${NC}"
    cd - > /dev/null
fi

# Ask if user wants to launch tmux session
echo
read -p "Create tmux session with Claude Code? (y/n): " launch_tmux

if [[ "$launch_tmux" =~ ^[Yy]$ ]]; then
    worktree_name=$(basename "$dir_name")
    session_name=$(get_session_name "$worktree_name")

    if create_tmux_session "$dir_name" "$session_name"; then
        echo
        read -p "Attach to session now? (y/n): " attach_now
        if [[ "$attach_now" =~ ^[Yy]$ ]]; then
            tmux attach -t "$session_name"
        else
            echo -e "${BLUE}Session created! Attach later with:${NC}"
            echo -e "  ${GREEN}tmux attach -t $session_name${NC}"
            echo -e "  ${GREEN}# Or use: ./claude-worktree.sh → option 6${NC}"
        fi
    fi
else
    echo -e "${BLUE}Worktree created successfully!${NC}"
    echo -e "To use it: ${GREEN}cd $dir_name${NC}"
    if [[ ! "$start_docker" =~ ^[Yy]$ ]]; then
        echo -e "To start Docker: ${GREEN}cd $dir_name && docker compose up -d --build${NC}"
    fi
fi