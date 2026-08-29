import sys, os
ml = r"D:\Users\viaco\PycharmProjects\minimax-ccr\dist_nu\ccrg\ml_lib"
sys.path.insert(0, ml)
lines = []
def t(name):
    try:
        m = __import__(name)
        ver = getattr(m, "__version__", "?")
        lines.append(f"OK   {name} {ver}")
    except Exception as e:
        lines.append(f"FAIL {name}: {type(e).__name__}: {e}")
t("torch")
t("transformers")
t("sentence_transformers")
try:
    from sentence_transformers import SentenceTransformer
    lines.append("OK   SentenceTransformer")
except Exception as e:
    lines.append(f"FAIL SentenceTransformer: {type(e).__name__}: {e}")
print("\n".join(lines))
