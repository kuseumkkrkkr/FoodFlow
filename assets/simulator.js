(function () {
  const formatter = new Intl.NumberFormat("ko-KR");
  const LOCAL_API_BASE = "http://127.0.0.1:8002/";
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function money(value) {
    return `${formatter.format(Math.round(Number(value || 0)))}원`;
  }

  function number(value) {
    return formatter.format(Math.round(Number(value || 0)));
  }

  function render(target, html) {
    if (target) target.innerHTML = html;
  }

  function normalizeBaseUrl(value) {
    return value.endsWith("/") ? value : `${value}/`;
  }

  function configuredApiBase() {
    const params = new URLSearchParams(window.location.search);
    const queryValue = params.get("apiBase") || "";
    const globalValue = typeof window.__FOODFLOW_API_BASE__ === "string" ? window.__FOODFLOW_API_BASE__ : "";
    const attrValue = document.documentElement.dataset.apiBase || "";
    return [queryValue, globalValue, attrValue].map(value => String(value || "").trim()).find(Boolean) || "";
  }

  function apiBase() {
    const configured = configuredApiBase();
    if (configured) return normalizeBaseUrl(configured);

    const { protocol, hostname, port, origin } = window.location;
    if (protocol === "file:") return "";
    if ((hostname === "localhost" || hostname === "127.0.0.1") && port && port !== "8002") {
      return LOCAL_API_BASE;
    }
    return normalizeBaseUrl(origin);
  }

  function apiUrl(path) {
    const base = apiBase();
    if (!base) return "";
    return new URL(path.replace(/^\/+/, ""), base).toString();
  }

  function apiBaseLabel() {
    return (apiBase() || "미설정").replace(/\/$/, "");
  }

  function selectedClaims() {
    return $$("input[name='claim']:checked").map(input => input.value);
  }

  function currentPayload() {
    return {
      idea: $("#idea")?.value?.trim() || "",
      category: $("#category")?.value || "sauce",
      package_type: $("#packageType")?.value || "pouch",
      qty: Math.max(100, Number($("#qty")?.value || 0)),
      budget: Math.max(0, Number($("#budget")?.value || 0)),
      claims: selectedClaims(),
    };
  }

  function pulseResult() {
    const resultSurface = $("#fitPill")?.closest(".glass");
    if (!resultSurface) return;
    resultSurface.classList.remove("result-pulse");
    void resultSurface.offsetWidth;
    resultSurface.classList.add("result-pulse");
  }

  function renderBanner(data) {
    const banner = $("#processBanner");
    if (!banner) return;
    banner.classList.remove("hidden");
    render(
      banner,
      `
        <div class="process-banner-head">
          <span class="badge">${escapeHtml(data.process_banner.headline)}</span>
        </div>
        <strong>${escapeHtml(data.process_banner.chain)}</strong>
        <p>${escapeHtml(data.process_banner.summary)}</p>
        <div class="meta-row">
          <span class="badge">${escapeHtml(data.package_label)}</span>
          <span class="badge">${escapeHtml(data.fit_pill)}</span>
        </div>
      `
    );
  }

  function renderSpec(data) {
    render(
      $("#panel-spec"),
      [
        `<article class="result-card"><span>제품 기획안</span><p>${escapeHtml(data.idea)}</p></article>`,
        `<article class="result-card"><span>공정 간단 설명</span><p>${escapeHtml(data.process_banner.summary)}</p></article>`,
        `<article class="result-card"><span>검토 문구</span><p>${escapeHtml(data.claims_text)}</p></article>`,
        `<article class="result-card"><span>공장 문의 질문</span><p>${escapeHtml((data.checks || []).map(item => `${item} 가능 여부`).join(" · "))}</p></article>`,
      ].join("")
    );
  }

  function renderVendors(data) {
    const vendors = data.vendors || [];
    if (!vendors.length) {
      render($("#panel-vendors"), `<article class="vendor"><strong>후보 없음</strong><p>현재 조건에서는 실제 기업 후보를 고르기 어려워 수량이나 포장을 조정해야 합니다.</p></article>`);
      return;
    }
    render(
      $("#panel-vendors"),
      vendors
        .map(
          vendor => `
            <article class="vendor">
              <div class="vendor-head">
                <div>
                  <strong>${escapeHtml(vendor.company_name)}</strong>
                  <p>${escapeHtml(vendor.summary || "실제 기업 페이지 연결")}</p>
                </div>
                <span class="vendor-score">${escapeHtml(vendor.score)}점</span>
              </div>
              <p>${escapeHtml(vendor.llm_reason)}</p>
              <div class="tag-row">
                <span class="badge">MOQ ${number(vendor.min_qty)}</span>
                <span class="badge">납기 ${number(vendor.lead_time_days)}일</span>
                <span class="badge">${escapeHtml((vendor.certifications || []).slice(0, 2).join(", ") || vendor.verification_status)}</span>
              </div>
              <div class="vendor-actions" style="margin-top: 12px;">
                <div class="meta-row" style="margin-top: 0;">
                  ${(vendor.product_keywords || [])
                    .slice(0, 3)
                    .map(keyword => `<span class="badge">${escapeHtml(keyword)}</span>`)
                    .join("")}
                </div>
                <a class="vendor-link" href="${escapeHtml(vendor.source_url)}" target="_blank" rel="noreferrer noopener">기업 페이지 보기</a>
              </div>
            </article>
          `
        )
        .join("")
    );
  }

  function renderRisks(data) {
    render(
      $("#panel-risk"),
      (data.risks || [])
        .map(
          risk => `
            <article class="risk ${escapeHtml(risk.severity)}">
              <span class="badge">${escapeHtml(risk.category)}</span>
              <strong>${escapeHtml(risk.title)}</strong>
              <p>${escapeHtml(risk.detail)}</p>
              <small>${escapeHtml(risk.action)}</small>
            </article>
          `
        )
        .join("")
    );
  }

  function renderPrice(data) {
    const costs = data.costs || {};
    const priceDrivers = (data.price_reason?.drivers || [])
      .map(item => `<li class="list-item">${escapeHtml(item)}</li>`)
      .join("");
    const orderChecks = (data.order_draft?.checks || [])
      .map(item => `<li class="list-item">${escapeHtml(item)}</li>`)
      .join("");
    render(
      $("#panel-price"),
      `
        <article class="result-card">
          <span>가격 예상 근거</span>
          <strong>${money(costs.total_cost)}</strong>
          <p>${escapeHtml(data.price_reason?.summary || "")}</p>
          <div class="meta-row">
            <span class="badge">예상 단가 ${money(costs.unit_cost)}</span>
            <span class="badge">예산 차이 ${money(costs.budget_gap)}</span>
            <span class="badge">수수료 ${(Number(costs.brokerage_rate || 0) * 100).toFixed(0)}%</span>
          </div>
          <ul class="price-list">${priceDrivers}</ul>
        </article>
        <article class="result-card">
          <span>발주안 초안</span>
          <strong>${escapeHtml(data.title)}</strong>
          <p>${escapeHtml(data.order_draft?.summary || "")}</p>
          <ul class="check-list">${orderChecks}</ul>
        </article>
      `
    );
  }

  function renderSteps(data) {
    const processLines = (data.process_lines || [])
      .map(
        line => `
          <article class="result-card">
            <span>공정 ${escapeHtml(line.order)}</span>
            <strong>${escapeHtml(line.name)}</strong>
            <p>${escapeHtml(line.summary)}</p>
          </article>
        `
      )
      .join("");
    const actions = (data.execution_steps || [])
      .map(
        (step, index) => `
          <article class="step-card">
            <div class="step-num">${index + 1}</div>
            <h3>${escapeHtml(step.title)}</h3>
            <p>${escapeHtml(step.detail)}</p>
          </article>
        `
      )
      .join("");
    render($("#panel-steps"), `${processLines}${actions}`);
  }

  function setTabs() {
    $$(".tab").forEach(tab => {
      tab.addEventListener("click", () => {
        $$(".tab").forEach(item => item.classList.remove("active"));
        tab.classList.add("active");
        ["spec", "vendors", "risk", "price", "steps"].forEach(name => {
          $(`#panel-${name}`)?.classList.toggle("hidden", tab.dataset.tab !== name);
        });
      });
    });
  }

  function applyResult(data) {
    $("#decisionBadge").textContent = data.decision_badge || "검토 가능";
    $("#resultTitle").textContent = data.title || "발주안 초안";
    $("#fitPill").textContent = data.fit_pill || "예산 검토중";
    $("#supplyCost").textContent = money(data.costs?.supply_cost);
    $("#sampleCost").textContent = money(data.costs?.sample_cost);
    $("#brokerage").textContent = money(data.costs?.brokerage_cost);
    $("#totalCost").textContent = money(data.costs?.total_cost);
    if ($("#flowMessage")) {
      $("#flowMessage").textContent = "";
      $("#flowMessage").classList.add("hidden");
    }
    renderBanner(data);
    renderSpec(data);
    renderVendors(data);
    renderRisks(data);
    renderPrice(data);
    renderSteps(data);
    pulseResult();
  }

  async function runModel() {
    const submitButton = $("#flowForm button[type='submit']");
    const message = $("#flowMessage");
    try {
      if (submitButton) submitButton.disabled = true;
      if (message) {
        message.classList.remove("hidden");
        message.textContent = "발주안 초안과 매칭 근거를 생성 중입니다.";
      }
      const targetUrl = apiUrl("api/simulate");
      if (!targetUrl) {
        throw new Error("file:// 경로에서는 API 주소를 찾을 수 없습니다. http://127.0.0.1:8002 또는 로컬 서버 주소로 여세요.");
      }
      const response = await fetch(targetUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentPayload()),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      applyResult(data);
    } catch (error) {
      if (message) {
        message.classList.remove("hidden");
        const detail = error instanceof Error ? error.message : "알 수 없는 오류";
        message.textContent = `실행 실패: ${detail}. 현재 API 기준 주소 ${apiBaseLabel()}.`;
      }
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  }

  function initAnimations() {
    const revealTargets = [
      ...$$(".section-head"),
      ...$$(".card-grid > *"),
      ...$$(".market-grid > *"),
      ...$$(".flow-grid > *"),
      ...$$(".test-copy"),
      ...$$(".test-grid > .glass"),
      ...$$(".footer"),
    ];

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    revealTargets.forEach((target, index) => {
      target.classList.add("reveal");
      target.style.setProperty("--delay", `${(index % 4) * 80}ms`);
      if (reducedMotion) target.classList.add("is-visible");
    });

    if (reducedMotion || !("IntersectionObserver" in window)) {
      revealTargets.forEach(target => target.classList.add("is-visible"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries, activeObserver) => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          activeObserver.unobserve(entry.target);
        });
      },
      { threshold: 0.16, rootMargin: "0px 0px -8% 0px" }
    );

    revealTargets.forEach(target => observer.observe(target));
  }

  $("#flowForm")?.addEventListener("submit", event => {
    event.preventDefault();
    runModel();
  });

  $("#notifyForm")?.addEventListener("submit", event => {
    event.preventDefault();
    const email = $("#notifyEmail")?.value?.trim() || "";
    $("#notifyMessage").textContent = email ? "알림 신청이 임시 저장되었습니다." : "이메일 주소를 입력해 주세요.";
  });

  setTabs();
  initAnimations();
  runModel();
})();
