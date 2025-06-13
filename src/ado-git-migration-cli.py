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

Test:
    python3 src/ado-git-migration-cli.py \
    --org-url https://dev.azure.com/Contoso-University \
    --project Fabric \
    --prod-repo test-migration-prod \
    --non-prod-repo test-migration-non-prod \
    --target test-consolidated-migration-repo \
    --nonprod-branch main \
    --dev-branch main \
    --verbose
"""
import argparse
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

def run(cmd: List[str], cwd: str | Path | None = None) -> None:
    """Run a shell command and raise on failure."""
    logging.debug(f'Running: {cmd} (cwd={cwd})')
    completed = subprocess.run(cmd, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, cmd, output=None, stderr=None)
    
def parse_args() -> argparse.Namespace:
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
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')

    return parser.parse_args()

def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s | %(message)s',
    )

    logging.info('Starting Azure DevOps defaults')
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

    prod_url = f"{args.org_url}/{args.project}/_git/{args.prod_repo}"
    nonprod_url = f"{args.org_url}/{args.project}/_git/{args.non_prod_repo}"
    target_url = f"{args.org_url}/{args.project}/_git/{args.target}"

    logging.info(f'Creating empty repository {args.target}')

    # az repos create fails if the repo already exists
    try:
        run(['az', 'repos', 'create', '--name', args.target, '--open'])
    except subprocess.CalledProcessError:
        logging.warning('Repository %s already exists, skipping creation', args.target)

    logging.info(f'Importing prod history into {args.target}')
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

    logging.info('Cloning target report and bringing in non-prod history')
    with tempfile.TemporaryDirectory() as tempdir:
        tempdir = Path(tempdir)
        target_clone_path = Path(tempdir) / 'merged'
        run(['git', 'clone', target_url, str(target_clone_path)])

        # add non-prod remote and fetch
        run(['git', 'remote', 'add', 'nonprod', nonprod_url], cwd=target_clone_path)
        run(['git', 'fetch', 'nonprod'], cwd=target_clone_path)

        # create develop branch from non-prod default branch
        run(['git', 'checkout', '-b', args.dev_branch, f'nonprod/{args.nonprod_branch}'], cwd=target_clone_path)

        run(
            ['git', 'push', '-u', 'origin', args.dev_branch],
            cwd=target_clone_path
        )

        logging.info(f'Migration finished.  Repo {args.target} now has\n'
                     f'main -> prod history\n'
                     f'{args.dev_branch} -> non-prod history')
        
if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as e:
        logging.error(f'Command failed {e.cmd}')
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        logging.info('Interrupted by user')
        sys.exit(130)