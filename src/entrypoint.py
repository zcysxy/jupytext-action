import os
import re
import json
from glob import iglob
import subprocess as sp
from typing import List, Tuple, Dict
import yaml


GITHUB_EVENT_NAME = os.environ['GITHUB_EVENT_NAME']

# Set repository
CURRENT_REPOSITORY = os.environ['GITHUB_REPOSITORY']
TARGET_REPOSITORY = os.environ['INPUT_TARGET_REPOSITORY'] or CURRENT_REPOSITORY
PULL_REQUEST_REPOSITORY = os.environ['INPUT_PULL_REQUEST_REPOSITORY'] or TARGET_REPOSITORY
REPOSITORY = PULL_REQUEST_REPOSITORY if GITHUB_EVENT_NAME == 'pull_request' else TARGET_REPOSITORY

# Set branches
GITHUB_REF = os.environ['GITHUB_REF']
GITHUB_HEAD_REF = os.environ['GITHUB_HEAD_REF']
GITHUB_BASE_REF = os.environ['GITHUB_BASE_REF']
CURRENT_BRANCH = GITHUB_HEAD_REF or GITHUB_REF.rsplit('/', 1)[-1]
TARGET_BRANCH = os.environ['INPUT_TARGET_BRANCH'] or CURRENT_BRANCH
PULL_REQUEST_BRANCH = os.environ['INPUT_PULL_REQUEST_BRANCH'] or GITHUB_BASE_REF
BRANCH = PULL_REQUEST_BRANCH if GITHUB_EVENT_NAME == 'pull_request' else TARGET_BRANCH

GITHUB_ACTOR = os.environ['GITHUB_ACTOR']
GITHUB_REPOSITORY_OWNER = os.environ['GITHUB_REPOSITORY_OWNER']
GITHUB_TOKEN = os.environ['INPUT_GITHUB_TOKEN']

# Command related inputs
CHECK = os.environ.get('INPUT_CHECK', 'frontmatter')  # 'all' | 'latest'
COMMENT_MAGICS = os.environ.get('INPUT_COMMENT_MAGICS', '') or 'false' # 'true' | 'false'
SPLIT_AT_HEADING = os.environ.get('INPUT_SPLIT_AT_HEADING', '') or 'false'  # 'true' | 'false'
SYNC_MODE = os.environ['INPUT_SYNC_MODE'] or 'one-way'  # 'one-way' | 'two-way'
FRONTMATTER_FIELD = os.environ.get('INPUT_FRONTMATTER_FIELD', '') or 'notebook'  # Field name in frontmatter to check
FRONTMATTER_VALUE = os.environ.get('INPUT_FRONTMATTER_VALUE', '') or 'true'  # Value in frontmatter field that indicates conversion
DISABLE_GIT_COMMIT = os.environ.get('INPUT_DISABLE_GIT_COMMIT', '') or 'false'  # Whether to disable Git commit
INPUT_DIRECTORY = os.environ.get('INPUT_INPUT_DIRECTORY', '') or './'  # Directory containing input files

# Format specifications
INPUT_FORMAT = os.environ['INPUT_INPUT_FORMAT'] or 'md'  # ipynb, py, md, R, etc.
OUTPUT_FORMAT = os.environ['INPUT_OUTPUT_FORMAT'] or 'ipynb'  # ipynb, py, md, R, etc.
OUTPUT_DIR = os.environ['INPUT_OUTPUT_DIR'] or './jupyter/'

# Mapping of format names to file extensions
FORMAT_TO_EXT = {
    'py': 'py',
    'python': 'py',
    'ipynb': 'ipynb', 
    'notebook': 'ipynb',
    'md': 'md',
    'markdown': 'md',
    'rmd': 'Rmd',
    'r': 'R',
    'rmarkdown': 'Rmd',
    'julia': 'jl',
    'c++': 'cpp',
    'scripts': 'py'  # Default for scripts
}

# Get input and output extensions
INPUT_EXT = FORMAT_TO_EXT.get(INPUT_FORMAT.lower(), INPUT_FORMAT.lower())
OUTPUT_EXT = FORMAT_TO_EXT.get(OUTPUT_FORMAT.lower(), OUTPUT_FORMAT.lower())

COMMIT_MESSAGE = os.environ['INPUT_COMMIT_MESSAGE'] or f"Convert {INPUT_FORMAT} to {OUTPUT_FORMAT} using jupytext"


def prepare_command(input_file: str, output_file: str) -> str:
    """Prepare the jupytext command for conversion."""
    command = "jupytext"
    
    # Add options
    if COMMENT_MAGICS == 'true':
        command += " --opt comment_magics=true"
    
    if SPLIT_AT_HEADING == 'true':
        command += " --opt split_at_heading=true"
    
    # Determine the conversion direction
    if INPUT_EXT == 'ipynb' and OUTPUT_EXT != 'ipynb':
        # Converting from notebook to text
        command += f" --to {OUTPUT_FORMAT} {input_file} -o {output_file}"
    elif INPUT_EXT != 'ipynb' and OUTPUT_EXT == 'ipynb':
        # Converting from text to notebook
        command += f" --to notebook {input_file} -o {output_file}"
    else:
        # Converting between text formats
        command += f" --to {OUTPUT_FORMAT} {input_file} -o {output_file}"
        
    return command


def get_all_files() -> List[str]:
    """Get list of all input files in the specified directory."""
    search_pattern = os.path.join(INPUT_DIRECTORY, f'**/*.{INPUT_EXT}')
    files = list(iglob(search_pattern, recursive=True))
    return files


def get_modified_files() -> List[str]:
    """Get list of modified files in the current commit within the input directory."""
    cmd = 'git diff-tree --no-commit-id --name-only -r HEAD'
    committed_files = sp.getoutput(cmd).split('\n')
    
    # Filter files that are in the input directory, have the correct extension, and exist
    input_dir_path = os.path.normpath(INPUT_DIRECTORY)
    files = [file for file in committed_files if (
        file.endswith(f'.{INPUT_EXT}') and 
        os.path.isfile(file) and
        (INPUT_DIRECTORY == './' or file.startswith(input_dir_path))
    )]
    
    return files


def get_files_with_frontmatter() -> List[str]:
    """Get list of Markdown files in the input directory that have the specified frontmatter field with the specified value."""
    if INPUT_FORMAT.lower() != 'md' and INPUT_FORMAT.lower() != 'markdown':
        print("Frontmatter check is only available for Markdown files.")
        return []
    
    # First, get all modified files
    cmd = 'git diff-tree --no-commit-id --name-only -r HEAD'
    committed_files = sp.getoutput(cmd).split('\n')
    
    # Filter for markdown files in the input directory
    input_dir_path = os.path.normpath(INPUT_DIRECTORY)
    modified_md_files = [file for file in committed_files if (
        file.endswith(f'.{INPUT_EXT}') and 
        os.path.isfile(file) and
        (INPUT_DIRECTORY == './' or file.startswith(input_dir_path))
    )]
    
    files_to_convert = []
    
    for file_path in modified_md_files:
        try:
            with open(file_path, 'r') as f:
                content = f.read()
                
            # Check for YAML frontmatter at the very beginning of the file
            # The pattern ensures nothing (not even whitespace) comes before the opening '---'
            frontmatter_match = re.match(r'\A---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
            if frontmatter_match:
                frontmatter_text = frontmatter_match.group(1)
                try:
                    # First try parsing as JSON (for {"author": "me"} style)
                    try:
                        if frontmatter_text.strip().startswith('{') and frontmatter_text.strip().endswith('}'):
                            frontmatter = json.loads(frontmatter_text)
                        else:
                            # Parse the standard YAML frontmatter
                            frontmatter = yaml.safe_load(frontmatter_text)
                    except json.JSONDecodeError:
                        # If JSON parsing fails, fall back to YAML
                        frontmatter = yaml.safe_load(frontmatter_text)
                    
                    # Check if the specified frontmatter field has the specified value
                    if frontmatter and isinstance(frontmatter, dict):
                        field_value = frontmatter.get(FRONTMATTER_FIELD)
                        
                        # Convert expected value to appropriate type for comparison
                        expected_value = FRONTMATTER_VALUE
                        if isinstance(expected_value, str):
                            if expected_value.lower() == 'true':
                                expected_value = True
                            elif expected_value.lower() == 'false':
                                expected_value = False
                            elif expected_value.isdigit():
                                expected_value = int(expected_value)
                        
                        if field_value == expected_value:
                            files_to_convert.append(file_path)
                except (yaml.YAMLError, json.JSONDecodeError) as e:
                    print(f"Error parsing frontmatter in {file_path}: {e}")
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
    
    return files_to_convert


def convert_files(files: List[str]) -> List[str]:
    """Convert input files to output format."""
    output_files = []
    for input_file in files:
        # Create output directory if it doesn't exist
        input_dir, input_name = os.path.split(input_file)
        # Strip INPUT_DIRECTORY from input_dir if it is set
        if INPUT_DIRECTORY and INPUT_DIRECTORY != './':
            input_dir = os.path.relpath(input_dir, INPUT_DIRECTORY)
        output_dir = os.path.join(OUTPUT_DIR, input_dir) if OUTPUT_DIR != './' else input_dir
        
        if not os.path.exists(output_dir) and output_dir:
            sp.call(f'mkdir -p {output_dir}', shell=True)
        
        # Determine output filename
        base_name = os.path.splitext(input_name)[0]
        output_file = os.path.join(output_dir, f"{base_name}.{OUTPUT_EXT}")
        output_files.append(output_file)
        
        # Prepare and run command
        command = prepare_command(input_file, output_file)
        print(f"Converting: {input_file} -> {output_file}")
        print(f"Command: {command}")
        result = sp.call(command, shell=True)
        
        if result != 0:
            print(f"Error converting {input_file}. Command failed with exit code {result}")
    
    return output_files


def sync_changes(source_files: List[str], target_files: List[str]) -> None:
    """Sync changes between source and target files in two-way mode."""
    if SYNC_MODE != 'two-way':
        return
        
    print("Running two-way sync...")
    for source, target in zip(source_files, target_files):
        if os.path.exists(source) and os.path.exists(target):
            # Get last modified times
            source_mtime = os.path.getmtime(source)
            target_mtime = os.path.getmtime(target)
            
            if source_mtime > target_mtime:
                # Source is newer, convert source to target format
                command = prepare_command(source, target)
                print(f"Syncing changes from {source} to {target}")
                sp.call(command, shell=True)
            elif target_mtime > source_mtime:
                # Target is newer, convert target back to source format
                reverse_command = prepare_command(target, source)
                print(f"Syncing changes from {target} to {source}")
                sp.call(reverse_command, shell=True)


def commit_changes(files: List[str]):
    """Commits changes."""
    # Skip if no files to commit
    if not files:
        print("No files to commit")
        return
        
    # Configure git - fix for workspace ownership issues
    sp.call('git config --global --add safe.directory /github/workspace', shell=True)
    
    # Configure git with actor info - use --global instead of --local
    set_email = f'git config --global user.email "{GITHUB_ACTOR}@users.noreply.github.com"'
    set_user = f'git config --global user.name "{GITHUB_ACTOR}"'
    sp.call(set_email, shell=True)
    sp.call(set_user, shell=True)
    
    # Prepare file list (deduplicate using set)
    file_list = ' '.join(set(files))
    
    # Commit changes - remove the checkout command as it might cause issues
    git_add = f'git add {file_list}'
    git_commit = f'git commit -m "{COMMIT_MESSAGE}"'
    
    print(f'Committing {file_list}...')
    
    try:
        sp.check_call(git_add, shell=True)
        # Use try/except to handle case where there might be nothing to commit
        try:
            sp.check_call(git_commit, shell=True)
            return True
        except sp.CalledProcessError:
            print("Nothing to commit - files may be unchanged")
            return False
    except sp.CalledProcessError as e:
        print(f"Git operation failed: {e}")
        return False


def push_changes():
    """Pushes commit."""
    set_url = f'git remote set-url origin https://x-access-token:{GITHUB_TOKEN}@github.com/{TARGET_REPOSITORY}'
    git_push = f'git push origin {TARGET_BRANCH}'
    sp.call(set_url, shell=True)
    sp.call(git_push, shell=True)


def main():
    # Exit early if this is a PR from a fork by non-owner
    if (GITHUB_EVENT_NAME == 'pull_request') and (GITHUB_ACTOR != GITHUB_REPOSITORY_OWNER):
        print("Skipping action for fork PR from non-owner")
        return
    
    # Ensure output directory exists
    if OUTPUT_DIR and OUTPUT_DIR != './':
        sp.call(f'mkdir -p {OUTPUT_DIR}', shell=True)
    
    # Get files to process
    if CHECK:
        if CHECK == 'all':
            input_files = get_all_files()
        elif CHECK == 'latest':
            input_files = get_modified_files()
        elif CHECK == 'frontmatter':
            input_files = get_files_with_frontmatter()
        else:
            raise ValueError(f'{CHECK} is a wrong value. Expecting "all", "latest", or "frontmatter"')
    else:
        input_files = []
    
    if not input_files:
        print(f'No {INPUT_FORMAT} files found to convert.')
        return
        
    print(f"Found {len(input_files)} files to process: {input_files}")
    
    # Convert files
    output_files = convert_files(input_files)
    
    # For two-way sync, check if any output files need to be synced back
    if SYNC_MODE == 'two-way':
        sync_changes(input_files, output_files)
        
    # Commit and push changes if any files were converted and git commit is not disabled
    if output_files:
        if DISABLE_GIT_COMMIT.lower() == 'true':
            print("Git commit disabled. Files were converted but not committed.")
            # List the output files for reference
            for file in output_files:
                print(f"- {file}")
        else:
            files_to_commit = output_files
            if SYNC_MODE == 'two-way':
                files_to_commit.extend(input_files)  # Also commit input files in two-way mode
                
            commit_changes(files_to_commit)
            push_changes()
    else:
        print('No files were converted successfully.')


if __name__ == '__main__':
    main()
