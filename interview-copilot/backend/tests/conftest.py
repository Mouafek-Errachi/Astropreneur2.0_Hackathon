import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# A throwaway database for the whole run (must be set before app.config is imported)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/")
