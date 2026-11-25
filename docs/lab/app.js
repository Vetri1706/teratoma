"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const data = window.AEGIS_DEMOS;
  if (
    !data ||
    data.schema_version !== 1 ||
    !Array.isArray(data.scenarios) ||
    !data.scenarios.length
  ) {
    $("error").hidden = false;
    $("error").textContent =
      "Replay data could not load. From the repository root, run python -m src.demo, then open artifacts/demo/index.html.";
    $("play").disabled = true;
    return;
  }
  const scenarios = data.scenarios;
  $("scenario-count").textContent =
    `${scenarios.length} SCENARIO${scenarios.length === 1 ? "" : "S"}`;
  const positions = [
    [340, 52],
    [568, 149],
    [482, 294],
    [198, 294],
    [112, 149],
  ];
  const NS = "http://www.w3.org/2000/svg";
  let selected = scenarios.findIndex(
    (scenario) => scenario.id === "primary-crash",
  );
  if (selected < 0) selected = 0;
  let frameIndex = 0;
  let timer = null;
  let lastPhase = "";
  const scenarioButtons = [];
  const svg = (name, attrs, text) => {
    const element = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs))
      element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  };
  function pause() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    $("play").textContent = "▶ Play replay";
    $("play").setAttribute("aria-label", "Play replay");
  }
  function render() {
    const scenario = scenarios[selected];
    const frame = scenario.frames[frameIndex];
    $("timeline").value = String(frameIndex);
    $("timeline").setAttribute(
      "aria-valuetext",
      `Virtual tick ${frame.tick}: ${frame.phase.name}`,
    );
    $("tick").textContent = frame.tick;
    $("completed").textContent = frame.completed;
    $("checks").textContent = frame.checks.toLocaleString();
    $("pending").textContent = frame.pending;
    $("previous").disabled = !scenario.milestones.some(
      (item) => item.tick < frame.tick,
    );
    $("next").disabled = !scenario.milestones.some(
      (item) => item.tick > frame.tick,
    );
    if (lastPhase !== frame.phase.name) {
      $("phase-name").textContent = frame.phase.name;
      $("phase-detail").textContent = frame.phase.detail;
      lastPhase = frame.phase.name;
    }
    const edges = document.createDocumentFragment();
    for (let a = 0; a < positions.length; a++) {
      for (let b = a + 1; b < positions.length; b++) {
        const blocked = frame.blocked.some(
          ([from, to]) => (from === a && to === b) || (from === b && to === a),
        );
        edges.append(
          svg("line", {
            x1: positions[a][0],
            y1: positions[a][1],
            x2: positions[b][0],
            y2: positions[b][1],
            class: `network-edge${blocked ? " blocked" : ""}`,
          }),
        );
      }
    }
    $("edges").replaceChildren(edges);
    const nodes = document.createDocumentFragment();
    const rows = document.createDocumentFragment();
    frame.nodes.forEach((node) => {
      const [x, y] = positions[node.id];
      const primary = node.alive && node.status === "normal" && node.primary;
      const state = !node.alive ? "stopped" : node.status;
      const style = !node.alive
        ? "stopped"
        : primary
          ? "primary"
          : node.status === "normal"
            ? "normal"
            : "recovering";
      const group = svg("g", {});
      group.append(
        svg(
          "title",
          {},
          `Node ${node.id}: ${state}, view ${node.view}, commit ${node.commit}${primary ? ", local primary" : ""}`,
        ),
      );
      group.append(
        svg("circle", { cx: x, cy: y, r: 34, class: `node-circle ${style}` }),
      );
      group.append(
        svg(
          "text",
          { x, y: y - 3, class: `node-name ${style}` },
          `N${node.id}`,
        ),
      );
      group.append(
        svg(
          "text",
          { x, y: y + 13, class: `node-state ${style}` },
          !node.alive
            ? "OFFLINE"
            : primary
              ? `P · V${node.view}`
              : `V${node.view}`,
        ),
      );
      group.append(
        svg(
          "text",
          { x, y: y + 51, class: "node-caption" },
          `${state} · commit ${node.commit}`,
        ),
      );
      nodes.append(group);
      const row = document.createElement("tr");
      [
        node.id,
        `${state}${primary ? " / primary" : ""}`,
        node.view,
        node.commit,
        node.log,
        node.recoveries,
        node.digest.slice(0, 16),
      ].forEach((value, index) => {
        const cell = document.createElement(index === 0 ? "th" : "td");
        if (index === 0) cell.scope = "row";
        cell.textContent = String(value);
        if (index === 6) cell.title = node.digest;
        row.append(cell);
      });
      rows.append(row);
    });
    $("replicas").replaceChildren(nodes);
    $("node-table").replaceChildren(rows);
  }
  function seek(tick) {
    pause();
    frameIndex = scenarios[selected].frames.findIndex(
      (frame) => frame.tick >= tick,
    );
    if (frameIndex < 0) frameIndex = scenarios[selected].frames.length - 1;
    render();
  }
  function choose(index) {
    pause();
    selected = index;
    frameIndex = 0;
    lastPhase = "";
    const scenario = scenarios[index];
    scenarioButtons.forEach((button, i) =>
      button.setAttribute("aria-pressed", String(i === index)),
    );
    $("scenario-title").textContent = scenario.title;
    $("question").textContent = scenario.question;
    $("seed").textContent = `SEED ${scenario.seed}`;
    $("timeline").max = scenario.frames.length - 1;
    $("result").textContent = scenario.result.passed ? "Converged ✓" : "Failed";
    $("result-detail").textContent =
      `${scenario.result.transactions} completions. Equal commit index and storage digest on all five replicas at the end.`;
    $("milestones").replaceChildren();
    scenario.milestones.forEach((milestone) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${milestone.tick} / ${milestone.title}`;
      button.title = milestone.detail;
      button.addEventListener("click", () => seek(milestone.tick));
      $("milestones").append(button);
    });
    render();
  }
  scenarios.forEach((scenario, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "scenario";
    button.setAttribute("aria-pressed", "false");
    const icon = document.createElement("span");
    icon.className = "scenario-icon";
    icon.textContent = String(index + 1).padStart(2, "0");
    icon.setAttribute("aria-hidden", "true");
    const content = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = scenario.title;
    const subtitle = document.createElement("p");
    subtitle.textContent = scenario.summary;
    content.append(title, subtitle);
    button.append(icon, content);
    button.addEventListener("click", () => choose(index));
    scenarioButtons.push(button);
    $("scenario-list").append(button);
  });
  function play() {
    if (frameIndex >= scenarios[selected].frames.length - 1) frameIndex = 0;
    $("play").textContent = "Ⅱ Pause";
    $("play").setAttribute("aria-label", "Pause replay");
    timer = window.setInterval(
      () => {
        frameIndex += 1;
        render();
        if (frameIndex >= scenarios[selected].frames.length - 1) pause();
      },
      Number($("speed").value),
    );
  }
  $("play").addEventListener("click", () =>
    timer === null ? play() : pause(),
  );
  $("speed").addEventListener("change", () => {
    if (timer !== null) {
      pause();
      play();
    }
  });
  $("timeline").addEventListener("input", (event) => {
    pause();
    frameIndex = Number(event.target.value);
    render();
  });
  $("previous").addEventListener("click", () => {
    const frame = scenarios[selected].frames[frameIndex];
    const previous = scenarios[selected].milestones
      .filter((item) => item.tick < frame.tick)
      .pop();
    if (previous) seek(previous.tick);
  });
  $("next").addEventListener("click", () => {
    const frame = scenarios[selected].frames[frameIndex];
    const next = scenarios[selected].milestones.find(
      (item) => item.tick > frame.tick,
    );
    if (next) seek(next.tick);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pause();
  });
  $("copy-command").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText("python -m src.demo");
      $("copy-command").textContent = "Copied ✓";
    } catch {
      $("copy-command").textContent = "Select and copy the command above";
    }
  });
  choose(selected);
})();
