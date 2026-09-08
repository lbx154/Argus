import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MapComposer, type MapComposerProps } from "../map/MapComposer";
import { MESSAGE_ATTACHMENT_ACCEPT } from "../lib/attachments";

const props: MapComposerProps = {
  value: "Check these measurements",
  onChange: () => {},
  onSend: async () => false,
  attachments: [],
  onAttachmentsChange: () => {},
  pending: false,
  onCancel: () => {},
  focusSignal: 0,
  sessionName: "Study",
  historical: false,
  zh: false,
};

it("exposes a native file picker through the Argus button using the chat file policy", () => {
  const html = renderToStaticMarkup(<MapComposer {...props} />);
  expect(html).toContain('type="file"');
  expect(html).toContain(`accept="${MESSAGE_ATTACHMENT_ACCEPT}"`);
  expect(html).toContain("map-attach");
  expect(html).toContain('aria-label="attach files"');
});

it("stays a collapsed pill while empty, even when the camera focuses a card", () => {
  const html = renderToStaticMarkup(
    <MapComposer {...props} value="" overview={false} />,
  );
  expect(html).toContain('data-compact="true"');
  expect(html).toContain("Ask Argus");
});

it("keeps the collapse control while a draft exists and labels the pill with the saved draft", () => {
  const html = renderToStaticMarkup(<MapComposer {...props} />);
  expect(html).toContain('data-compact="false"');
  expect(html).toContain('aria-label="Collapse message composer"');
  expect(html).toContain("Draft saved");
  const zhHtml = renderToStaticMarkup(<MapComposer {...props} zh />);
  expect(zhHtml).toContain('aria-label="收起消息输入"');
  expect(zhHtml).toContain("草稿已保留");
});

it("keeps the selected attachment visible and disables removal while a send is pending", () => {
  const html = renderToStaticMarkup(
    <MapComposer
      {...props}
      pending
      attachments={[
        new File(["phi,n\n0.9,120"], "study.csv", { type: "text/csv" }),
      ]}
    />,
  );
  expect(html).toContain("study.csv");
  expect(html).toMatch(
    /<button[^>]*disabled=""[^>]*aria-label="remove attachment study.csv"/,
  );
  expect(html).toContain("Stop waiting");
});
