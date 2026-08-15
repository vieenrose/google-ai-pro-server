import Route from "@ember/routing/route";
import getURL from "discourse/lib/get-url";

// The quota monitor is a plain server-rendered page at /quota (visible to
// all users). The Ember route exists only so the admin plugin card shows a
// valid link; it redirects to the real page.
export default class AdminPluginsAntigravityQuotaRoute extends Route {
  beforeModel() {
    window.location.href = getURL("/quota");
  }
}
