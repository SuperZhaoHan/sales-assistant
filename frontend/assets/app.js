/**
 * 私域销售跟进 Agent —— 前端（零构建，纯 ES Module）。
 *
 * 设计取舍：
 * 1. 不走 npm 打包，静态文件直接由 nginx 托管，部署只需要一个 nginx 镜像；
 * 2. 前端只调用两个接口：/v1/chat 与 /v1/chat/stream，其它字段全部透传，
 *    这样后端加能力（模板、工具、确认）时前端几乎不用改；
 * 3. 每次调用都把 TTFB、总耗时、token、费用显示出来——这是「可观测」能力
 *    在界面上的落点。
 */

const API_BASE = "/v1";

const els = {
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  statusMeta: document.getElementById("status-meta"),
  messages: document.getElementById("messages"),
  empty: document.getElementById("empty"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  reset: document.getElementById("reset"),
  layout: document.getElementById("layout"),
  debug: document.getElementById("debug"),
  debugRequest: document.getElementById("debug-request"),
  debugResponse: document.getElementById("debug-response"),
  toggleDebug: document.getElementById("toggle-debug"),
  copyJson: document.getElementById("copy-json"),
  stream: document.getElementById("stream"),
  templateName: document.getElementById("template-name"),
  templateVersion: document.getElementById("template-version"),
  varProfile: document.getElementById("var-profile"),
  varHistory: document.getElementById("var-history"),
  model: document.getElementById("model"),
  temperature: document.getElementById("temperature"),
  maxTokens: document.getElementById("max-tokens"),
  tenantId: document.getElementById("tenant-id"),
  statCalls: document.getElementById("stat-calls"),
  statTokens: document.getElementById("stat-tokens"),
  statCost: document.getElementById("stat-cost"),
  statLatency: document.getElementById("stat-latency"),
};

/** 会话内累计指标。 */
const session = { calls: 0, tokens: 0, cost: 0, latency: 0 };

/** 对话历史（只发 role/content，避免把界面状态混进请求体）。 */
let history = [];

/** 正在进行的请求，用于取消。 */
let inflight = null;

class ApiError extends Error {
  constructor(status, body) {
    const err = body && body.error ? body.error : {};
    super(err.message || `HTTP ${status}`);
    this.status = status;
    this.code = err.code || `HTTP_${status}`;
    this.details = err.details || null;
    this.requestId = err.request_id || null;
    this.retryable = err.retryable;
  }
}

/* ------------------------------------------------------------------ 组装请求 */

function optionalNumber(raw) {
  const value = raw.trim();
  if (!value) return undefined;
  const num = Number(value);
  return Number.isFinite(num) ? num : undefined;
}

function buildPayload() {
  const payload = {
    messages: history.map(({ role, content }) => ({ role, content })),
    subject_id: "web-console",
  };

  const tenant = els.tenantId.value.trim();
  if (tenant) payload.tenant_id = tenant;

  const model = els.model.value.trim();
  if (model) payload.model = model;

  const temperature = optionalNumber(els.temperature.value);
  if (temperature !== undefined) payload.temperature = temperature;

  const maxTokens = optionalNumber(els.maxTokens.value);
  if (maxTokens !== undefined) payload.max_tokens = maxTokens;

  const templateName = els.templateName.value;
  if (templateName) {
    payload.template_name = templateName;
    const version = els.templateVersion.value.trim();
    if (version) payload.template_version = version;

    // 模板变量必须是非空字符串：空值会让后端渲染失败（VALIDATION_1001），
    // 这里直接不传，让后端的报错信息保持干净可控。
    const vars = {};
    if (els.varProfile.value.trim()) vars.profile = els.varProfile.value.trim();
    if (els.varHistory.value.trim()) vars.history = els.varHistory.value.trim();
    payload.template_vars = vars;
  }

  return payload;
}

/* ------------------------------------------------------------------ 消息渲染 */

function pill(text, kind = "") {
  const el = document.createElement("span");
  el.className = `pill ${kind}`.trim();
  el.textContent = text;
  return el;
}

function hideEmpty() {
  if (els.empty && els.empty.parentNode) els.empty.remove();
}

function addUserMessage(text) {
  hideEmpty();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.append(bubble);
  els.messages.append(wrap);
  scrollToEnd();
}

function addAssistantMessage() {
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  bubble.append(cursor);
  wrap.append(bubble);
  els.messages.append(wrap);
  scrollToEnd();

  return {
    bubble,
    setText(text) {
      bubble.textContent = text;
      bubble.append(cursor);
      scrollToEnd();
    },
    finish(text) {
      bubble.textContent = text;
      cursor.remove();
    },
    /** 在气泡下面挂一行指标。 */
    setMeta(metrics) {
      if (!metrics || !metrics.length) return;
      const meta = document.createElement("div");
      meta.className = "meta";
      for (const [text, kind] of metrics) meta.append(pill(text, kind));
      wrap.append(meta);
    },
    markStop(reason) {
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.append(pill(reason, "warn"));
      wrap.append(meta);
    },
  };
}

function addErrorMessage(err) {
  hideEmpty();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble error";

  const title = document.createElement("div");
  title.className = "error-title";
  title.textContent = `${err.code} · HTTP ${err.status}`;
  bubble.append(title);

  const body = document.createElement("div");
  body.textContent = err.message;
  bubble.append(body);

  const meta = document.createElement("div");
  meta.className = "meta";
  if (err.requestId) meta.append(pill(`request_id ${err.requestId}`));
  if (err.retryable) meta.append(pill("可重试", "warn"));
  if (err.details) meta.append(pill(JSON.stringify(err.details).slice(0, 160)));
  wrap.append(bubble, meta);
  els.messages.append(wrap);
  scrollToEnd();
}

function scrollToEnd() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

/* ------------------------------------------------------------------ 指标统计 */

function recordUsage(usage, latencyMs) {
  if (!usage) return;
  session.calls += 1;
  session.tokens += usage.total_tokens || 0;
  session.cost += usage.cost_usd || 0;
  if (Number.isFinite(latencyMs)) session.latency += latencyMs;
  renderStats();
}

function renderStats() {
  els.statCalls.textContent = String(session.calls);
  els.statTokens.textContent = session.tokens.toLocaleString("en-US");
  els.statCost.textContent = `$${session.cost.toFixed(6)}`;
  els.statLatency.textContent = session.calls
    ? `${Math.round(session.latency / session.calls)} ms`
    : "—";
}

function buildMetricList({ ttfb, latencyMs, usage, stopReason, template }) {
  const list = [];
  if (ttfb !== null && ttfb !== undefined) list.push([`首字 ${Math.round(ttfb)} ms`]);
  if (Number.isFinite(latencyMs)) list.push([`总耗时 ${Math.round(latencyMs)} ms`]);
  if (usage) {
    list.push([`in ${usage.input_tokens} / out ${usage.output_tokens}`]);
    list.push([`$${(usage.cost_usd || 0).toFixed(6)}`]);
  }
  if (template) list.push([`模板 ${template.name}${template.version}`, "accent"]);
  if (stopReason && stopReason !== "end_turn") list.push([stopReason, "warn"]);
  return list;
}

/* ------------------------------------------------------------------ 请求发送 */

async function sendStreaming(payload, message) {
  const controller = new AbortController();
  inflight = controller;
  setSending(true);

  const started = performance.now();
  let ttfb = null;
  let text = "";
  let doneEvent = null;

  try {
    const res = await fetch(`${API_BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new ApiError(res.status, body);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");

        const dataLine = block
          .split("\n")
          .map((line) => line.trim())
          .find((line) => line.startsWith("data:"));
        if (!dataLine) continue;

        const raw = dataLine.slice(5).trim();
        if (raw === "[DONE]") continue;

        let event;
        try {
          event = JSON.parse(raw);
        } catch {
          continue;
        }

        if (event.error) throw new ApiError(res.status, { ok: false, error: event.error });
        if (event.delta) {
          if (ttfb === null) ttfb = performance.now() - started;
          text += event.delta;
          message.setText(text);
        } else if (event.done) {
          doneEvent = event;
        }
      }
    }
  } catch (error) {
    if (error.name === "AbortError") {
      message.finish(text || "（已取消）");
      message.markStop("客户端取消");
      return { aborted: true };
    }
    message.bubble.parentNode?.remove();
    throw error;
  } finally {
    inflight = null;
    setSending(false);
  }

  const latencyMs = doneEvent?.latency_ms;
  const usage = doneEvent?.usage;
  const template = doneEvent?.prompt_template;

  message.finish(text);
  message.setMeta(
    buildMetricList({ ttfb, latencyMs, usage, stopReason: "end_turn", template }),
  );
  recordUsage(usage, latencyMs);

  return { text, usage, latencyMs, template, ttfb };
}

async function sendOnce(payload, message) {
  const controller = new AbortController();
  inflight = controller;
  setSending(true);

  const started = performance.now();
  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const body = await res.json().catch(() => null);
    if (!res.ok || !body?.ok) throw new ApiError(res.status, body);

    const data = body.data;
    message.finish(data.text);
    message.setMeta(
      buildMetricList({
        ttfb: null,
        latencyMs: data.latency_ms ?? performance.now() - started,
        usage: data.usage,
        stopReason: data.stop_reason || "end_turn",
        template: data.prompt_template,
      }),
    );
    recordUsage(data.usage, data.latency_ms);
    return data;
  } catch (error) {
    if (error.name === "AbortError") {
      message.finish("（已取消）");
      message.markStop("客户端取消");
      return null;
    }
    message.bubble.parentNode?.remove();
    throw error;
  } finally {
    inflight = null;
    setSending(false);
  }
}

function setSending(isSending) {
  if (isSending) {
    els.send.textContent = "停止";
    els.send.classList.add("is-stop");
    els.send.disabled = false;
  } else {
    els.send.textContent = "发送";
    els.send.classList.remove("is-stop");
    els.send.disabled = false;
  }
}

/* ------------------------------------------------------------------ 主流程 */

async function submit(text) {
  const content = text.trim();
  if (!content) return;

  els.input.value = "";
  autoGrow();
  addUserMessage(content);

  history.push({ role: "user", content });
  const payload = buildPayload();
  els.debugRequest.textContent = JSON.stringify(payload, null, 2);

  const message = addAssistantMessage();
  let result = null;

  try {
    result = els.stream.checked ? await sendStreaming(payload, message) : await sendOnce(payload, message);
  } catch (error) {
    if (error instanceof ApiError) {
      addErrorMessage(error);
      els.debugResponse.textContent = JSON.stringify(
        {
          status: error.status,
          code: error.code,
          message: error.message,
          details: error.details,
          request_id: error.requestId,
        },
        null,
        2,
      );
    } else {
      addErrorMessage({ code: "CLIENT_ERROR", status: 0, message: error.message });
    }
    // 失败的这一轮不进历史，避免把错误当成上下文带下去。
    history.pop();
    return;
  }

  if (result?.aborted || result === null) {
    history.pop();
    return;
  }

  const answer = result.text ?? "";
  if (answer) history.push({ role: "assistant", content: answer });

  els.debugResponse.textContent = JSON.stringify(
    {
      text: answer.slice(0, 400),
      usage: result.usage,
      latency_ms: result.latencyMs,
      prompt_template: result.template,
      ttfb_ms: result.ttfb ?? null,
    },
    null,
    2,
  );
}

/* ------------------------------------------------------------------ 健康检查 */

async function refreshHealth() {
  try {
    const res = await fetch("/health/ready", { cache: "no-store" });
    const body = await res.json().catch(() => null);
    if (res.ok && body?.status === "ready") {
      els.statusDot.className = "dot dot-ok";
      els.statusText.textContent = "后端就绪";
      els.statusMeta.textContent = `${body.env} · ${body.version}`;
    } else {
      els.statusDot.className = "dot dot-bad";
      els.statusText.textContent = body?.status === "not_ready" ? "未就绪" : `不可用 ${res.status}`;
      els.statusMeta.textContent = body ? `${body.env ?? ""}`.trim() : "";
    }
  } catch {
    els.statusDot.className = "dot dot-bad";
    els.statusText.textContent = "连不上后端";
    els.statusMeta.textContent = "";
  }
}

/* ------------------------------------------------------------------ 交互绑定 */

function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 180)}px`;
}

els.send.addEventListener("click", () => {
  if (inflight) {
    inflight.abort();
    return;
  }
  submit(els.input.value);
});

els.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!inflight) submit(els.input.value);
  }
});

els.input.addEventListener("input", autoGrow);

els.reset.addEventListener("click", () => {
  // 会话状态全部在内存里，直接刷新页面即等价于「开一段新会话」，
  // 也顺手清掉了后端返回的历史上下文。
  location.reload();
});

els.toggleDebug.addEventListener("click", () => {
  const open = els.debug.hidden;
  els.debug.hidden = !open;
  els.toggleDebug.setAttribute("aria-pressed", String(open));
  els.layout.classList.toggle("with-debug", open);
});

els.copyJson.addEventListener("click", async () => {
  const text = `请求:\n${els.debugRequest.textContent}\n\n响应:\n${els.debugResponse.textContent}`;
  try {
    await navigator.clipboard.writeText(text);
    els.copyJson.textContent = "已复制";
    setTimeout(() => (els.copyJson.textContent = "复制"), 1200);
  } catch {
    els.copyJson.textContent = "复制失败";
  }
});

for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => submit(chip.textContent));
}

renderStats();
refreshHealth();
setInterval(refreshHealth, 15000);
autoGrow();
