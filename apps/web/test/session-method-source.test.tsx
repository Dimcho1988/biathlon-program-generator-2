import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SessionMethodGuidance, sessionMethods } from "../components/session-method-guidance";
import { MethodCard } from "../components/training-method-library";
import { methodReasonText, type LibraryMethod, type SessionMethod } from "../lib/method-library";
import type { DraftSession } from "../lib/training-management";

const session = (method: SessionMethod) => ({ sport: "Run", method_library: method, blocks: [], dose_evidence: {} } as unknown as DraftSession);
const method: SessionMethod = {
  id: "OF-TEST", title: "Адаптиран метод", derived_from: ["SOURCE-TEST"],
  source: { authors: "Автори, 2024", reference: "Публикация от запазения източник", links: ["https://doi.org/example"] },
  original: { work: "4 × 4 мин", recovery: "3 мин", notes: "Оригинална бележка" },
  adaptation: { recipe_text: "3 × 4 мин", is_source_equivalent: false, notes: "Промяна на броя повторения" },
};

it("shows the real catalog's author, original prescription and publication link inside session guidance", () => {
  const catalog = JSON.parse(readFileSync(new URL("../../../biathlon/data/training_method_library.json", import.meta.url), "utf8"));
  const original = catalog.methods.find((entry: SessionMethod) => entry.id === "R01") as SessionMethod;
  const html = renderToStaticMarkup(<SessionMethodGuidance session={session(original)}/>);
  const visible = html.slice(0, html.indexOf("<details>"));
  expect(visible).toContain("Yang et al., 2026");
  expect(visible).toContain("Оригинално предписание");
  expect(visible).toContain("4 повторения");
  expect(visible).toContain('href="https://doi.org/10.5114/biolsport.2026.156227"');
  expect(visible).toContain('href="/methods?method=R01"');
  expect(html).toContain("Адаптация за onFlows");
  expect(html).toContain("не е идентична с оригиналното предписание");
});

it("keeps original, adaptation and original-library access separate", () => {
  const html = renderToStaticMarkup(<SessionMethodGuidance session={session(method)}/>);
  expect(html).toContain('href="/methods?method=SOURCE-TEST"');
  expect(html).toContain("отвори оригинала в базата");
  expect(html).toContain("4 × 4 мин");
  expect(html).toContain("3 × 4 мин");
  expect(html).toContain("Оригинална бележка");
  expect(html).toContain("Промяна на броя повторения");
});

it("retains sources for nested combined parts and enriches a sparse legacy duplicate without changing frozen resolution", () => {
  const first = { id: method.id, title: method.title, resolution: { status: "READY", target_speed_kmh: 15 } };
  const combined = session({ id: "COMBO-1", title: "Комбинация", components: [{ id: "COMBO-2", components: [first] }] });
  combined.blocks = [{ parent_method_card: { ...method, resolution: { status: "READY", target_speed_kmh: 20 } } }] as DraftSession["blocks"];
  const cards = sessionMethods(combined);
  expect(cards.map(card => card.id)).toEqual(["COMBO-1", "COMBO-2", "OF-TEST"]);
  expect(cards[2].source).toEqual(method.source);
  expect(cards[2].resolution?.target_speed_kmh).toBe(15);
  const html = renderToStaticMarkup(<SessionMethodGuidance session={combined}/>);
  expect(html.match(/href="https:\/\/doi.org\/example"/g)).toHaveLength(1);
  expect(html).not.toContain('href="/methods?method=COMBO-');
});

it("never invents an external publication for a coach profile or a missing source", () => {
  const coach = session({ id: "COACH-TEST", source: { authors: "onFlows", reference: "Работен треньорски профил", links: ["javascript:alert(1)", "file:///source.pdf", "not-a-url"] }, adaptation: "Индивидуален треньорски вариант." });
  const html = renderToStaticMarkup(<SessionMethodGuidance session={coach}/>);
  expect(html).toContain("Работен треньорски профил");
  expect(html).toContain("Индивидуален треньорски вариант");
  expect(html).not.toContain("DOI · публикация");
  expect(html).not.toContain("javascript:");
  expect(html).not.toContain("file:");
  expect(html).not.toContain("Оригинално предписание");
});

it("labels a native coach recipe as its working basis rather than a published protocol", () => {
  const coach: LibraryMethod = { id: "COACH-TEST", title: "Треньорски интервали", sports: ["Run"],
    periods: ["GENERAL_PREPARATION"], disciplines: [], guidance: {}, registry_kind: "COACH_PROFILE",
    source: { authors: "onFlows", reference: "Работен треньорски профил" },
    original: { work: "60 сек", repetitions: "8 повторения", recovery: "60 сек" },
    adaptation: "Индивидуален треньорски вариант.",
  };
  for (const html of [
    renderToStaticMarkup(<SessionMethodGuidance session={session(coach)}/>),
    renderToStaticMarkup(<MethodCard method={coach} canEdit={false} selected onChange={() => {}} onReset={() => {}}/>),
  ]) {
    expect(html).toContain("Базова треньорска рецепта");
    expect(html).toContain("8 повторения");
    expect(html).not.toContain("Публикувано предписание");
    expect(html).not.toContain("Публикация:");
  }
  expect(methodReasonText("VARIANT_REQUIRES_COACH_ACTIVATION")).toBe("Този съкратен вариант се включва изрично от треньора.");
});

it("preserves a book title, page locator and provided filename without inventing external access", () => {
  const book = session({ id: "BOOK-TEST", source: { authors: "Laursen & Buchheit",
    reference: "Science and Application of High-Intensity Interval Training",
    locator: "с. 254", provided_file: "interval_methods.pdf", links: [] },
    original: { work: "Работата е описана в книгата." },
  });
  const html = renderToStaticMarkup(<SessionMethodGuidance session={book}/>);
  expect(html).toContain("Science and Application of High-Intensity Interval Training");
  expect(html).toContain("с. 254");
  expect(html).toContain("Източников файл:");
  expect(html).toContain("interval_methods.pdf");
  expect(html).toContain('href="/methods?method=BOOK-TEST"');
  expect(html).not.toContain('href="http');
  expect(html).not.toContain('href="file:');
  expect(html).not.toContain("DOI · публикация");
});
