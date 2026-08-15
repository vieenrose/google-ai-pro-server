import Route from "@ember/routing/route";
import getURL from "discourse/lib/get-url";

// The real page is server-rendered (Rails). The Ember route exists only so
// the admin plugin list shows the link; it redirects to the server page.
export default class AdminPluginsAntigravityQuotaRoute extends Route {
  beforeModel() {
    window.location.href = getURL("/admin/plugins/antigravity-quota/full");
  }
}
