import queue

# Runtime state variables
ACTIVE_MESSAGES = []
ACTIVE_FILES = set()
AGENT_MODIFIED_FILES = {}
FILE_CHANGES_QUEUE = queue.Queue()
EXPAND_TOOL_OUTPUT = False
