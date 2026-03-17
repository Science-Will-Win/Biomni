import base64
import io
import sys
from io import StringIO

# Create a persistent namespace that will be shared across all executions
_persistent_namespace = {}

# Global list to store captured plots
_captured_plots = []


def run_python_repl(command: str) -> str:
    """Executes the provided Python command in a persistent environment and returns the output.
    Variables defined in one execution will be available in subsequent executions.
    """

    def execute_in_repl(command: str) -> str:
        """Helper function to execute the command in the persistent environment."""
        old_stdout = sys.stdout
        sys.stdout = mystdout = StringIO()

        # Use the persistent namespace
        global _persistent_namespace

        try:
            # Apply matplotlib monkey patches before execution
            _apply_matplotlib_patches()

            # Execute the command in the persistent namespace
            exec(command, _persistent_namespace)
            output = mystdout.getvalue()

            # Capture any matplotlib plots that were generated
            # _capture_matplotlib_plots()

        except Exception as e:
            output = f"Error: {str(e)}"
        finally:
            sys.stdout = old_stdout
        return output

    command = command.strip("```").strip()
    return execute_in_repl(command)


def _capture_matplotlib_plots():
    """Capture any matplotlib plots that might have been generated during execution."""
    global _captured_plots
    try:
        import matplotlib.pyplot as plt

        # Check if there are any active figures
        if plt.get_fignums():
            for fig_num in plt.get_fignums():
                fig = plt.figure(fig_num)

                # Save figure to base64
                buffer = io.BytesIO()
                fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
                buffer.seek(0)

                # Convert to base64
                image_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                plot_data = f"data:image/png;base64,{image_data}"

                # Add to captured plots if not already there
                if plot_data not in _captured_plots:
                    _captured_plots.append(plot_data)

                # Close the figure to free memory
                plt.close(fig)

    except ImportError:
        # matplotlib not available
        pass
    except Exception as e:
        print(f"Warning: Could not capture matplotlib plots: {e}")


def _apply_matplotlib_patches():
    """Apply simple monkey patches to matplotlib functions to automatically capture plots."""
    try:
        import matplotlib.pyplot as plt

        # Only patch if matplotlib is available and not already patched
        if hasattr(plt, "_biomni_patched"):
            return

        # Store original functions
        original_show = plt.show
        original_savefig = plt.savefig

        def show_with_capture(*args, **kwargs):
            """Enhanced show function that captures plots before displaying them."""
            # Capture any plots before showing
            _capture_matplotlib_plots()
            # Print a message to indicate plot was generated
            print("Plot generated and displayed")
            # Call the original show function
            return original_show(*args, **kwargs)

        def savefig_with_capture(*args, **kwargs):
            """Enhanced savefig function that captures plots after saving them."""
            # Get the filename from args if provided
            filename = args[0] if args else kwargs.get("fname", "unknown")
            # Call the original savefig function
            result = original_savefig(*args, **kwargs)
            # Capture the plot after saving
            _capture_matplotlib_plots()
            # Print a message to indicate plot was saved
            print(f"Plot saved to: {filename}")
            return result

        # Replace functions with enhanced versions
        plt.show = show_with_capture
        plt.savefig = savefig_with_capture

        # Mark as patched to avoid double-patching
        plt._biomni_patched = True

    except ImportError:
        # matplotlib not available
        pass
    except Exception as e:
        print(f"Warning: Could not apply matplotlib patches: {e}")


def get_captured_plots():
    """Get all captured matplotlib plots."""
    global _captured_plots
    return _captured_plots.copy()


def clear_captured_plots():
    """Clear all captured matplotlib plots."""
    global _captured_plots
    _captured_plots = []


def read_function_source_code(function_name: str) -> str:
    """Read the source code of a function from any module path.

    Parameters
    ----------
        function_name (str): Fully qualified function name (e.g., 'bioagentos.tool.support_tools.write_python_code')

    Returns
    -------
        str: The source code of the function

    """
    import importlib
    import inspect

    # Split the function name into module path and function name
    parts = function_name.split(".")
    module_path = ".".join(parts[:-1])
    func_name = parts[-1]

    try:
        # Import the module
        module = importlib.import_module(module_path)

        # Get the function object from the module
        function = getattr(module, func_name)

        # Get the source code of the function
        source_code = inspect.getsource(function)

        return source_code
    except (ImportError, AttributeError) as e:
        return f"Error: Could not find function '{function_name}'. Details: {str(e)}"


# def request_human_feedback(question, context, reason_for_uncertainty):
#     """
#     Request human feedback on a question.

#     Parameters:
#         question (str): The question that needs human feedback.
#         context (str): Context or details that help the human understand the situation.
#         reason_for_uncertainty (str): Explanation for why the LLM is uncertain about its answer.

#     Returns:
#         str: The feedback provided by the human.
#     """
#     print("Requesting human feedback...")
#     print(f"Question: {question}")
#     print(f"Context: {context}")
#     print(f"Reason for Uncertainty: {reason_for_uncertainty}")

#     # Capture human feedback
#     human_response = input("Please provide your feedback: ")

#     return human_response


def download_synapse_data(
    entity_ids: str | list[str],
    download_location: str = ".",
    follow_link: bool = False,
    recursive: bool = False,
    timeout: int = 300,
    entity_type: str = "dataset",
):
    """Download data from Synapse using entity IDs.

    Uses the synapse CLI to download files, folders, or projects from Synapse.
    Requires SYNAPSE_AUTH_TOKEN environment variable for authentication.
    Automatically installs synapseclient if not available.

    CRITICAL: Always check entity type from query_synapse() search results or user hints and pass the correct entity_type!
    The default entity_type="dataset" may not be appropriate for your entity.

    IMPORTANT: Multiple entity IDs are only supported for entity_type="file".
    For datasets, folders, and projects, only a single entity_id is supported.

    Parameters
    ----------
    entity_ids : str or list of str
        Synapse entity ID(s) to download.
        - For files: Can be a single ID string or list of ID strings
        - For datasets/folders/projects: Must be a single ID string only
    download_location : str, default "."
        Directory where files will be downloaded (current directory by default)
    follow_link : bool, default False
        Whether to follow links to download the linked entity
    recursive : bool, default False
        Whether to recursively download folders and their contents
        ONLY valid for entity_type="folder" - ignored for other types
    timeout : int, default 300
        Timeout in seconds for each download operation
    entity_type : str, default "dataset"
        Type of Synapse entity ("dataset", "file", "folder", "project")
        MUST match the actual entity type from search results or user hints!
        The default "dataset" should only be used for actual datasets.
        Check the 'node_type' field in search results to determine correct type.

    Returns
    -------
    dict
        Dictionary containing download results and any errors

    Notes
    -----
    Requires SYNAPSE_AUTH_TOKEN environment variable with your Synapse personal
    access token for authentication.

    AGENT USAGE GUIDANCE:
    1. Always check the 'node_type' field from query_synapse() search results or user hints
    2. Pass the correct entity_type parameter matching the node_type
    3. Do NOT rely on the default entity_type="dataset" unless confirmed
    4. For multiple downloads, ensure all entities are of type "file"
    5. Only use recursive=True with entity_type="folder"

    Examples
    --------
    # After searching with query_synapse(), check node_type and use appropriate entity_type:

    # If search result shows 'node_type': 'dataset'
    download_synapse_data("syn123456", entity_type="dataset")

    # If search result shows 'node_type': 'file'
    download_synapse_data("syn654321", entity_type="file")

    # If search result shows 'node_type': 'folder'
    download_synapse_data("syn789012", entity_type="folder", recursive=True)

    # Multiple files (only if all are 'node_type': 'file')
    download_synapse_data(["syn111", "syn222"], entity_type="file")
    """
    import os
    import subprocess

    # Check for required authentication token
    synapse_token = os.environ.get("SYNAPSE_AUTH_TOKEN")
    if not synapse_token:
        return {
            "success": False,
            "error": "SYNAPSE_AUTH_TOKEN environment variable is required for downloading",
            "suggestion": "Set SYNAPSE_AUTH_TOKEN with your Synapse personal access token",
        }

    # Check if synapse CLI is available
    try:
        subprocess.run(["synapse", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            # Try to install synapseclient
            print("Installing synapseclient...")
            subprocess.run(["pip", "install", "synapseclient"], check=True)
            print("✓ synapseclient installed successfully")
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "error": f"Failed to install synapseclient: {e}",
                "suggestion": "Please install manually: pip install synapseclient",
            }

    # Ensure entity_ids is a list
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]

    # Validate that multiple IDs are only used with file entity type
    if len(entity_ids) > 1 and entity_type != "file":
        return {
            "success": False,
            "error": f"Multiple entity IDs are only supported for entity_type='file'. "
            f"For entity_type='{entity_type}', only a single entity_id is supported.",
            "suggestion": "Use a single entity_id string instead of a list, or change entity_type to 'file'",
        }

    # Validate that recursive is only used with folder entity type
    if recursive and entity_type != "folder":
        return {
            "success": False,
            "error": f"recursive=True is only valid for entity_type='folder'. "
            f"For entity_type='{entity_type}', recursive should be False.",
            "suggestion": "Set recursive=False, or change entity_type to 'folder' if appropriate",
        }

    # Create download directory if it doesn't exist
    os.makedirs(download_location, exist_ok=True)

    results = []
    errors = []

    for entity_id in entity_ids:
        try:
            # Build synapse download command with authentication
            if entity_type == "dataset":
                # For datasets, use query syntax to download the actual files
                cmd = [
                    "synapse",
                    "-p",
                    synapse_token,
                    "get",
                    "-q",
                    f"select * from {entity_id}",
                    "--downloadLocation",
                    download_location,
                ]
            else:
                # For files, folders, projects, use direct ID
                cmd = ["synapse", "-p", synapse_token, "get", entity_id, "--downloadLocation", download_location]

            # Add recursive flag only for folders (validation above ensures recursive is only True for folders)
            if entity_type == "folder" and recursive:
                cmd.append("-r")

            if follow_link:
                cmd.append("--followLink")

            # Execute download
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)

            results.append(
                {
                    "entity_id": entity_id,
                    "success": True,
                    "stdout": result.stdout,
                    "download_location": download_location,
                }
            )

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to download {entity_id}: {e.stderr if e.stderr else str(e)}"
            errors.append(error_msg)
            results.append({"entity_id": entity_id, "success": False, "error": error_msg})
        except subprocess.TimeoutExpired:
            error_msg = f"Download timeout for {entity_id} (>{timeout} seconds)"
            errors.append(error_msg)
            results.append({"entity_id": entity_id, "success": False, "error": error_msg})

    # Summary
    successful_downloads = [r for r in results if r["success"]]
    failed_downloads = [r for r in results if not r["success"]]

    return {
        "success": len(failed_downloads) == 0,
        "total_requested": len(entity_ids),
        "successful": len(successful_downloads),
        "failed": len(failed_downloads),
        "download_location": download_location,
        "results": results,
        "errors": errors if errors else None,
    }


# ═══════════════════════════════════════════
# Helper functions for tool/data inspection
# ═══════════════════════════════════════════

def inspect_tool(tool_name: str) -> str:
    """Inspect a Biomni tool to get its full parameter info and import path.

    Parameters
    ----------
    tool_name : str
        Name of the tool function to inspect (e.g., "analyze_circular_dichroism_spectra").

    Returns
    -------
    str
        Formatted string with module path, import example, required and optional parameters.
    """
    try:
        from biomni.utils import read_module2api
        module2api = read_module2api()
    except Exception as e:
        return f"Error loading tool registry: {e}"

    for module_name, tools in module2api.items():
        for tool in tools:
            if tool.get("name") == tool_name:
                lines = [f"Tool: {tool_name}", f"Module: {module_name}"]
                lines.append(f"Import: from {module_name} import {tool_name}")
                lines.append("")

                desc = tool.get("description", "")
                if desc:
                    lines.append(f"Description: {desc}")
                    lines.append("")

                req = tool.get("required_parameters", [])
                if req:
                    lines.append("Required parameters:")
                    for p in req:
                        default = p.get("default")
                        default_str = f" (default: {default})" if default is not None else ""
                        lines.append(f"  - {p.get('name', '?')}: {p.get('type', 'Any')}{default_str}")
                        if p.get("description"):
                            lines.append(f"    {p['description']}")

                opt = tool.get("optional_parameters", [])
                if opt:
                    lines.append("Optional parameters:")
                    for p in opt:
                        default = p.get("default", "None")
                        lines.append(f"  - {p.get('name', '?')}: {p.get('type', 'Any')} (default: {default})")
                        if p.get("description"):
                            lines.append(f"    {p['description']}")

                return "\n".join(lines)

    return f"Tool '{tool_name}' not found in registry."


def inspect_data(filename: str) -> str:
    """Inspect a data lake file to get its schema, columns, dtypes, and sample rows.

    Parameters
    ----------
    filename : str
        Name of the file in the data lake (e.g., "DepMap_CRISPRGeneDependency.csv").

    Returns
    -------
    str
        Formatted string with file path, shape, columns, dtypes, and sample rows.
    """
    import os

    # Resolve data lake path from environment
    data_base = os.environ.get("BIOMNI_DATA_PATH", "/app/data")
    data_lake_dir = os.path.join(data_base, "biomni_data", "data_lake")
    filepath = os.path.join(data_lake_dir, filename)

    if not os.path.exists(filepath):
        # Try direct path in case filename includes subdirectory
        if os.path.exists(os.path.join(data_lake_dir, filename)):
            filepath = os.path.join(data_lake_dir, filename)
        else:
            available = []
            try:
                available = sorted(os.listdir(data_lake_dir))[:20]
            except OSError:
                pass
            msg = f"File '{filename}' not found in data lake at {data_lake_dir}."
            if available:
                msg += f"\nAvailable files: {', '.join(available)}"
            return msg

    if os.path.isdir(filepath):
        try:
            items = sorted(os.listdir(filepath))
            total = len(items)
            shown = items[:20]
            lines = [f"Directory: {filepath}", f"Total items: {total}"]
            for item in shown:
                lines.append(f"  - {item}")
            if total > 20:
                lines.append(f"  ... and {total - 20} more")
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading directory: {e}"

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    try:
        import pandas as pd

        if ext == "parquet":
            df = pd.read_parquet(filepath)
        elif ext == "tsv":
            df = pd.read_csv(filepath, sep="\t", nrows=5)
            df_full_shape = pd.read_csv(filepath, sep="\t", usecols=[0])
            df_shape = (len(df_full_shape), len(df.columns))
        elif ext in ("csv", "txt"):
            df = pd.read_csv(filepath, nrows=5)
            df_full_shape = pd.read_csv(filepath, usecols=[0])
            df_shape = (len(df_full_shape), len(df.columns))
        else:
            return f"Unsupported file format: .{ext}. Supported: csv, tsv, parquet, txt"

        if ext == "parquet":
            df_shape = df.shape
            sample = df.head(3)
        else:
            sample = df.head(3)

        lines = [
            f"File: {filepath}",
            f"Shape: {df_shape[0]} rows x {df_shape[1]} columns",
            "",
            "Columns and dtypes:",
        ]
        for col in df.columns:
            lines.append(f"  - {col}: {df[col].dtype}")

        lines.append("")
        lines.append("Sample rows (first 3):")
        lines.append(sample.to_string(max_colwidth=50))

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading file: {e}"
