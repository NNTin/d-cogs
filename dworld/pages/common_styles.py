"""Common CSS styles for D-World dashboard pages."""


def get_common_styles() -> str:
    """
    Returns common CSS styles for D-World dashboard pages.

    This function provides a consistent Discord dark theme across all dashboard pages,
    eliminating duplicate CSS definitions.

    Returns:
        str: CSS style string to be embedded in dashboard page templates

    Usage:
        from ..common_styles import get_common_styles

        html_content = f'''
        <style>
            {get_common_styles()}
        </style>
        <div class="dworld-config">
            ...
        </div>
        '''
    """
    return """
        .dworld-config {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #1e1f22;
            color: #e6e6e6;
            padding: 20px;
            border-radius: 8px;
        }
        .dworld-config h1 {
            color: #ffffff;
            border-bottom: 2px solid #5865f2;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }
        .dworld-config h2 {
            color: #ffffff;
            margin-top: 30px;
            margin-bottom: 15px;
            font-size: 1.3em;
        }
        .config-section {
            background-color: #2b2e34;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 20px;
        }
        .config-item {
            margin-bottom: 12px;
            padding: 8px;
            background-color: #1e1f22;
            border-radius: 3px;
        }
        .config-label {
            font-weight: bold;
            color: #b9bbbe;
            display: inline-block;
            width: 180px;
        }
        .config-value {
            color: #ffffff;
            font-family: 'Courier New', monospace;
        }
        .color-preview {
            display: inline-block;
            width: 30px;
            height: 30px;
            border-radius: 3px;
            vertical-align: middle;
            margin-left: 10px;
            border: 2px solid #4f545c;
        }
        .form-section {
            background-color: #2b2e34;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 20px;
        }
        .form-section input[type="text"],
        .form-section input[type="color"] {
            background-color: #1e1f22;
            color: #ffffff;
            border: 1px solid #4f545c;
            padding: 8px;
            border-radius: 3px;
            width: 100%;
            max-width: 400px;
        }
        .form-section label {
            color: #b9bbbe;
            display: block;
            margin-top: 12px;
            margin-bottom: 5px;
            font-weight: 500;
        }
        .form-section input[type="submit"] {
            background-color: #5865f2;
            color: #ffffff;
            border: none;
            padding: 10px 20px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            margin-top: 15px;
            transition: background-color 0.2s;
        }
        .form-section input[type="submit"]:hover {
            background-color: #4752c4;
        }
        .form-section input[type="checkbox"] {
            margin-right: 8px;
        }
        .form-select {
            background-color: #1e1f22;
            color: #ffffff;
            border: 1px solid #4f545c;
            padding: 8px;
            border-radius: 3px;
            width: 100%;
            max-width: 400px;
            cursor: pointer;
            font-size: 14px;
        }
        .form-select:hover {
            border-color: #5865f2;
        }
        .explanation-text {
            color: #b9bbbe;
            font-style: italic;
            margin-bottom: 15px;
        }
        .pagination {
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 15px 0;
            gap: 10px;
        }
        .pagination a,
        .pagination span {
            background-color: #2b2e34;
            color: #e6e6e6;
            padding: 8px 12px;
            border-radius: 3px;
            text-decoration: none;
            transition: background-color 0.2s;
        }
        .pagination a:hover {
            background-color: #5865f2;
        }
        .pagination .current {
            background-color: #5865f2;
            font-weight: bold;
        }
        .search-box {
            margin-bottom: 15px;
        }
        .search-box input[type="text"] {
            width: 100%;
            max-width: 400px;
        }
"""
