export default function () {
  this.route("adminPlugins", { path: "/admin/plugins" }, function () {
    this.route("antigravity-quota", { path: "antigravity-quota" });
  });
}
