"""
TalentRadar — Flask Dashboard API
Serves the UI and REST endpoints consumed by the frontend.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from core.database import get_all_jobs, get_stats, update_status, init_db
from loguru import logger

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_ROOT, "config.yaml")) as f:
    _cfg = yaml.safe_load(f)

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)   # allow any origin — fine for a personal project


# ── Init DB once at startup ────────────────────────────────
@app.before_request
def _ensure_db():
    init_db()


# ── UI ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────────────────────
#  API ROUTES
# ─────────────────────────────────────────────────────────

# GET /api/health
@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "service": "TalentRadar"})


# GET /api/stats
@app.route("/api/stats")
def api_stats():
    """
    Returns:
      { total, pending, applied, skipped, urgent, aiml, devops }
    """
    try:
        return jsonify(get_stats())
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({"error": str(e)}), 500


# GET /api/jobs
# Query params:
#   resume_type = aiml | devops
#   status      = pending | applied | skipped
#   platform    = linkedin | shine | internshala
#   min_score   = float  (default 60)
#   limit       = int    (default 30)
@app.route("/api/jobs")
def api_jobs():
    """All jobs with optional filters."""
    try:
        jobs = get_all_jobs(
            resume_type = request.args.get("resume_type"),
            status      = request.args.get("status"),
            platform    = request.args.get("platform"),
            min_score   = request.args.get("min_score", default=60, type=float),
            limit       = request.args.get("limit",     default=30, type=int),
        )
        return jsonify(jobs)
    except Exception as e:
        logger.error(f"Jobs error: {e}")
        return jsonify({"error": str(e)}), 500


# GET /api/jobs/aiml
@app.route("/api/jobs/aiml")
def api_jobs_aiml():
    """Shortcut — AI/ML jobs only."""
    try:
        return jsonify(get_all_jobs(resume_type="aiml", limit=30))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# GET /api/jobs/devops
@app.route("/api/jobs/devops")
def api_jobs_devops():
    """Shortcut — DevOps jobs only."""
    try:
        return jsonify(get_all_jobs(resume_type="devops", limit=30))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# GET /api/jobs/applied
@app.route("/api/jobs/applied")
def api_jobs_applied():
    """Shortcut — jobs the user has applied to."""
    try:
        return jsonify(get_all_jobs(status="applied", min_score=0, limit=100))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# PATCH /api/jobs/<id>/status   body: { "status": "applied" }
@app.route("/api/jobs/<int:job_id>/status", methods=["PATCH"])
def api_update_status(job_id: int):
    """Mark a job as applied / skipped / pending."""
    try:
        body   = request.get_json(force=True)
        status = body.get("status", "")
        if status not in {"pending", "applied", "skipped"}:
            return jsonify({"error": "status must be pending | applied | skipped"}), 400
        update_status(job_id, status)
        logger.info(f"Job {job_id} → {status}")
        return jsonify({"ok": True, "job_id": job_id, "status": status})
    except Exception as e:
        logger.error(f"Update status error: {e}")
        return jsonify({"error": str(e)}), 500


# GET /api/run-status
@app.route("/api/run-status")
def api_run_status():
    """
    Returns the status of the latest scraping run.
    { status: 'running' | 'done' | 'idle', cycle: N, finished_at: '...' }
    Used by the frontend to know when new jobs are available.
    """
    try:
        from core.database import get_conn
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) as total FROM runs")
        cnt = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return jsonify({"status": "idle", "cycle": 0, "finished_at": None})
        return jsonify({
            "status":      row["status"],          # 'running' or 'done'
            "cycle":       cnt["total"] if cnt else 1,
            "finished_at": row["finished_at"],
        })
    except Exception as e:
        logger.error(f"Run-status error: {e}")
        return jsonify({"status": "idle", "cycle": 0, "finished_at": None})


# ── Run ────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("PORT", _cfg["dashboard"]["port"]))
    debug = os.environ.get("FLASK_ENV") != "production"
    logger.info(f"🚀 TalentRadar Dashboard → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)