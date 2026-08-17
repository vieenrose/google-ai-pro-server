export default function () {
  this.route("adminPlugins", { path: "/admin/plugins" }, function () {
    this.route("sloth-ai", { path: "sloth-ai" });
  });
}
