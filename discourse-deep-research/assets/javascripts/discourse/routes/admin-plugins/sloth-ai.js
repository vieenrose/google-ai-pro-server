import Route from "@ember/routing/route";
import getURL from "discourse/lib/get-url";

// The admin page is a plain server-rendered page at /admin/plugins/sloth-ai
// (settings + model management + quota). The Ember route exists only so the
// admin plugin card shows a valid link; it redirects to the real page.
export default class AdminPluginsSlothAiRoute extends Route {
  beforeModel() {
    window.location.href = getURL("/admin/plugins/sloth-ai/full");
  }
}
