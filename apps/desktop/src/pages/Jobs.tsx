import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { apiFetch } from "../api/client";
import type { JobRow } from "../api/types";

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  paused: "已暂停",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const TERMINAL = new Set(["done", "failed", "cancelled"]);

function basename(path: string | null): string {
  if (!path) return "";
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export default function Jobs() {
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await apiFetch("/jobs");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setJobs((await response.json()) as JobRow[]);
      setError("");
    } catch {
      setError("无法读取导入进度，请确认本地服务已启动。");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 1000);
    return () => clearInterval(timer);
  }, [load]);

  async function control(jobId: string, action: "pause" | "resume" | "cancel") {
    setBusyId(jobId);
    setError("");
    try {
      const response = await apiFetch(`/jobs/${jobId}/${action}`, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await load();
    } catch {
      setError("操作失败，请稍后重试。");
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>导入进度</h1>
          <p className="muted">扫描与索引进度每秒自动刷新。</p>
        </div>
        <div className="header-actions">
          <Link className="link-button" to="/">
            返回资料库
          </Link>
          <button type="button" className="secondary" onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            刷新
          </button>
        </div>
      </header>

      {error && <p className="notice error">{error}</p>}

      {jobs.length === 0 ? (
        <div className="empty">
          <p>还没有导入任务。</p>
          <Link className="button primary" to="/">
            去资料库选择文件夹
          </Link>
        </div>
      ) : (
        <div className="job-list">
          {jobs.map((job) => {
            const percent = Math.round(Math.min(Math.max(job.progress, 0), 1) * 100);
            const terminal = TERMINAL.has(job.status);
            return (
              <article className="job-card" key={job.id}>
                <header>
                  <div>
                    <h3>{job.type === "scan" ? "扫描导入" : job.type}</h3>
                    <p className="muted small" title={job.current_file ?? undefined}>
                      {job.current_file
                        ? basename(job.current_file)
                        : terminal
                          ? "任务已结束"
                          : "等待处理"}
                    </p>
                  </div>
                  <span className={`tag status-${job.status}`}>
                    {STATUS_LABELS[job.status] ?? job.status}
                  </span>
                </header>

                <div className="progress">
                  <div className="progress-bar" style={{ width: `${percent}%` }} />
                </div>
                <p className="muted small">
                  {job.processed} / {job.total} · {percent}%
                </p>

                {job.error && <p className="notice error small">{job.error}</p>}

                <div className="job-actions">
                  {(job.status === "running" || job.status === "queued") && (
                    <button
                      type="button"
                      className="secondary"
                      disabled={busyId === job.id}
                      onClick={() => void control(job.id, "pause")}
                    >
                      暂停
                    </button>
                  )}
                  {job.status === "paused" && (
                    <button
                      type="button"
                      className="secondary"
                      disabled={busyId === job.id}
                      onClick={() => void control(job.id, "resume")}
                    >
                      继续
                    </button>
                  )}
                  {!terminal && (
                    <button
                      type="button"
                      className="danger"
                      disabled={busyId === job.id}
                      onClick={() => void control(job.id, "cancel")}
                    >
                      取消
                    </button>
                  )}
                  {job.status === "done" && (
                    <Link className="button secondary" to="/">
                      查看资料库
                    </Link>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
