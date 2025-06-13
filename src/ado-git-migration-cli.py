"""
Merge a non-prod and prod ADO git repo into a single repo.

Requirements:
- Python >3.8
- Azure DevOps CLI (az devops) installed and configured
- Git installed and configured

Run:
This is expected to be run in an Ubuntu evironment (such as with ubuntu-latest in Azure DevOps pipelines)

Usage:
    python src/ado-git-migration-cli.py --org-url https://dev.azure.com/org \
        --project myproject \
        --prod-repo prod-repo-name \
        --non-prod-repo non-prod-repo-name \
        --target consolidated-repo-name \
        --nonprod-branch main \
        --dev-branch develop \
        --verbose

Arguments:
    --org-url: URL of the Azure DevOps organization (e.g., https://dev.azure.com/org)
    --project: Name of the Azure DevOps project
    --prod-repo: Name of the production repository
    --non-prod-repo: Name of the non-production repository
    --target: Name of the new consolidated repository to be created
    --nonprod-branch: Default branch name in the non-prod repo (default: main)
    --dev-branch: Branch name to use in the merged repo for non-prod history (default: develop)
    --pat: Personal Access Token for Azure DevOps authentication (optional)
    --username: Username for Azure DevOps authentication (optional, if not using PAT)
    -v, --verbose: Enable verbose output

Test:
    python3 src/ado-git-migration-cli.py \
    --org-url https://dev.azure.com/Contoso-University \
    --project Fabric \
    --prod-repo test-migration-prod \
    --non-prod-repo test-migration-non-prod \
    --target test-consolidated-migration-repo \
    --nonprod-branch main \
    --dev-branch develop \
    --pat <pat-token> \
    --verbose
"""
import argparse
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional
import urllib.parse

def run(cmd: List[str], cwd: str | Path | None = None, env: Optional[dict] = None) -> None:
    """
    Run a shell command and raise on failure.
    
    Args:
        cmd: List of command arguments to run
        cwd: Working directory for the command (default: current directory)
        env: Additional environment variables to set (default: None)
        
    Raises:
        subprocess.CalledProcessError: If the command returns non-zero exit code
    """
    # Combine current environment with any additional environment variables
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    
    logging.debug(f'Running: {cmd} (cwd={cwd})')
    completed = subprocess.run(cmd, cwd=cwd, check=False, env=run_env)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, cmd, output=None, stderr=None)
    
def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments.
    
    Returns:
        argparse.Namespace: The parsed command-line arguments
    """
    parser = argparse.ArgumentParser(
        description='Consolidate prod + non-prod Azure DevOps repos into one'
    )
    parser.add_argument('--org-url', required=True, help='https://dev.azure.com/org')
    parser.add_argument('--project', required=True, help='Azure DevOps project name')
    parser.add_argument('--prod-repo', required=True, help='prod repo name')
    parser.add_argument('--non-prod-repo', required=True, help='non-prod repo name')
    parser.add_argument('--target', required=True, help='Name of the new (to be created) consolidated repo')
    parser.add_argument('--nonprod-branch', default='main', help='Default branch name in the non-prod repo (default: main)')
    parser.add_argument('--dev-branch', default='develop', help='Branch name to use in the merged repo for non-prod history (*default: develop*)')
    parser.add_argument('--pat', help='Personal Access Token for Azure DevOps authentication')
    parser.add_argument('--username', help='Username for Azure DevOps authentication (if not using PAT)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')

    return parser.parse_args()

def get_auth_url(base_url: str, username: Optional[str] = None, pat: Optional[str] = None) -> str:
    """
    Create an authenticated URL for Git operations.
    
    Args:
        base_url: The base repository URL
        username: The username for authentication (optional)
        pat: Personal Access Token for authentication (optional)
        
    Returns:
        str: URL with authentication credentials embedded if provided
             Original URL if no credentials provided
    
    Note:
        Format: https://username:pat@dev.azure.com/org/project/_git/repo
    """
    if not pat:
        # If no PAT provided, return the original URL
        return base_url
    
    # Split the URL to insert authentication
    parsed = urllib.parse.urlparse(base_url)
    
    # Use provided username or default to the PAT itself for Basic Auth
    auth_user = username if username else pat
    
    # Reconstruct the URL with authentication
    netloc = f"{auth_user}:{pat}@{parsed.netloc}"
    authenticated_url = parsed._replace(netloc=netloc).geturl()
    
    return authenticated_url

def main() -> None:
    """
    Main function to execute the repository migration process.
    
    The function performs the following steps:
    1. Set up logging and parse command line arguments
    2. Configure Azure DevOps CLI defaults
    3. Create a new target repository
    4. Import production history into the target repo
    5. Clone the target repository
    6. Add the non-prod repository as a remote
    7. Create a development branch from the non-prod history
    8. Push the development branch to the target repository
    
    Raises:
        subprocess.CalledProcessError: If any of the commands fail
    """
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s | %(message)s',
    )

    # Check if PAT is provided directly or in environment variables
    pat = args.pat or os.environ.get('AZURE_DEVOPS_PAT')
    username = args.username or os.environ.get('AZURE_DEVOPS_USERNAME')
    
    # If PAT not provided directly, check if there's an existing token in the az CLI
    if not pat:
        logging.info("No PAT provided, will use existing Azure CLI authentication")
    
    logging.info('<----------------------- Starting Azure DevOps defaults --------------------------->')
    run(
        [
            'az',
            'devops',
            'configure',
            '--defaults',
            f'organization={args.org_url}',
            f'project={args.project}',
        ]
    )

    # Base URLs for repositories
    prod_url = f"{args.org_url}/{args.project}/_git/{args.prod_repo}"
    nonprod_url = f"{args.org_url}/{args.project}/_git/{args.non_prod_repo}"
    target_url = f"{args.org_url}/{args.project}/_git/{args.target}"
    
    # Create authenticated URLs if PAT is provided
    auth_target_url = get_auth_url(target_url, username, pat)

    logging.info(f'<----------------------- Creating empty repository {args.target} --------------------------->')

    # az repos create fails if the repo already exists
    try:
        run(['az', 'repos', 'create', '--name', args.target, '--open'])
    except subprocess.CalledProcessError:
        logging.warning(f'Repository {args.target} already exists, skipping creation')

    logging.info(f'<----------------------- Importing prod history into {args.target} --------------------------->')
    run(
        [
            'az',
            'repos',
            'import',
            'create',
            '--git-source-url',
            prod_url,
            '--repository',
            args.target,
        ]
    )

    logging.info('<----------------------- Cloning target report and bringing in non-prod history --------------------------->')
    with tempfile.TemporaryDirectory() as tempdir:
        tempdir = Path(tempdir)
        target_clone_path = Path(tempdir) / 'merged'
        
        # Clone using authenticated URL if PAT is available
        run(['git', 'clone', auth_target_url, str(target_clone_path)])

        # add non-prod remote and fetch
        auth_nonprod_url = get_auth_url(nonprod_url, username, pat)
        run(['git', 'remote', 'add', 'nonprod', auth_nonprod_url], cwd=target_clone_path)
        run(['git', 'fetch', 'nonprod'], cwd=target_clone_path)

        # create develop branch from non-prod default branch
        run(['git', 'checkout', '-b', args.dev_branch, f'nonprod/{args.nonprod_branch}'], cwd=target_clone_path)

        # Set Git config for credentials if PAT is available
        git_env = None
        if pat:
            # Configure Git credential helper for this process
            git_env = {
                'GIT_ASKPASS': 'echo',
                'GIT_USERNAME': username or pat,
                'GIT_PASSWORD': pat
            }
            # Configure Git to not prompt for credentials
            run(['git', 'config', 'credential.helper', 'store'], cwd=target_clone_path)
            
            # For debugging only - don't do this in production code!
            if args.verbose:
                logging.debug("Using authenticated Git push")

        # Push with authentication environment if PAT is provided
        run(
            ['git', 'push', '-u', 'origin', args.dev_branch],
            cwd=target_clone_path,
            env=git_env
        )

        logging.info(f'<----------------------- Migration finished.  Repo {args.target} now has\n'
                     f'main -> prod history\n'
                     f'{args.dev_branch} -> non-prod history --------------------------->')
        
if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as e:
        logging.error(f'Command failed {e.cmd}')
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        logging.info('Interrupted by user')
        sys.exit(130)