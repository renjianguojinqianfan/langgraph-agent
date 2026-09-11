/* ============================================================
   学习课程 · 可复用测验组件（无依赖，所有课程共用）
   用法：
     <div id="quiz1"></div>
     <script src="../assets/quiz.js"></script>
     <script>
       TeachQuiz.render("quiz1", {
         title: "路由预测测验",
         questions: [
           { q: "题面（支持 HTML）",
             opts: ["tool", "reflect", "finish"],   // 第 0 个必须是正确答案
             explain: "答完后显示的解析（支持 HTML）" }
         ]
       });
     </script>
   说明：渲染时会自动打乱选项顺序；每题点击即时反馈；
   全部作答后显示总分与「重做」按钮（选项重新洗牌）。
   ============================================================ */
(function () {
  "use strict";

  function shuffled(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function render(rootId, spec) {
    const root = document.getElementById(rootId);
    if (!root) return;

    let score = 0;
    let answered = 0;
    const total = spec.questions.length;

    function build() {
      score = 0; answered = 0;
      root.innerHTML = "";
      root.classList.add("quiz");

      const title = document.createElement("div");
      title.className = "quiz-title";
      title.textContent = spec.title || "随堂测验";
      root.appendChild(title);

      const progress = document.createElement("div");
      progress.className = "quiz-progress";
      progress.textContent = "点击你认为正确的选项，即时反馈。共 " + total + " 题";
      root.appendChild(progress);

      spec.questions.forEach(function (item, qi) {
        const card = document.createElement("div");
        card.className = "q-card";

        const qt = document.createElement("div");
        qt.className = "q-text";
        qt.innerHTML = "<b>Q" + (qi + 1) + ".</b> " + item.q;
        card.appendChild(qt);

        const opts = document.createElement("div");
        opts.className = "q-opts";
        const correctText = item.opts[0];
        shuffled(item.opts).forEach(function (optText) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "q-opt";
          btn.textContent = optText;
          btn.addEventListener("click", function () {
            if (card.dataset.done) return;
            card.dataset.done = "1";
            answered++;
            const isCorrect = optText === correctText;
            if (isCorrect) score++;
            Array.prototype.forEach.call(opts.children, function (b) {
              b.disabled = true;
              if (b.textContent === correctText) b.classList.add("correct");
              else if (b === btn) b.classList.add("wrong");
            });
            const ex = document.createElement("div");
            ex.className = "q-explain show";
            ex.innerHTML = (isCorrect ? "✅ 答对了。" : "❌ 正确答案是 <code>" +
              correctText.replace(/</g, "&lt;") + "</code>。") + item.explain;
            card.appendChild(ex);
            progress.textContent = "进度：" + answered + " / " + total +
              " · 当前得分 " + score;
            if (answered === total) finish();
          });
          opts.appendChild(btn);
        });
        card.appendChild(opts);
        root.appendChild(card);
      });
    }

    function finish() {
      const box = document.createElement("div");
      const pct = score / total;
      const level = pct >= 0.8 ? "good" : pct >= 0.5 ? "meh" : "bad";
      const word = pct >= 0.8 ? "漂亮，这个机制你已经拿下了。"
        : pct >= 0.5 ? "有感觉了，错题的解析再读一遍，然后点重做。"
        : "别急，回到讲义把路由规则重读一遍再来。";
      box.className = "q-score " + level;
      box.textContent = "得分 " + score + " / " + total + " · " + word;
      root.appendChild(box);

      const reset = document.createElement("button");
      reset.type = "button";
      reset.className = "q-reset";
      reset.textContent = "重做（选项会重新洗牌）";
      reset.addEventListener("click", build);
      root.appendChild(reset);
    }

    build();
  }

  window.TeachQuiz = { render: render };
})();
