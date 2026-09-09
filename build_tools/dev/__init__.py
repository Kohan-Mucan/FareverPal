"""Dev-only preview pages that never ship inside the app.

Everything here is a developer tool for looking at the UI/asset side of the app
without the game running. It lives under `build_tools/` rather than inside
`farever_companion/` on purpose:

* it is not part of the shipped product, so it must not be packaged, and a
  frozen build must not be able to reach it at all — the folder simply does not
  exist there;
* the line budget the shipped `ui/` has to meet does not apply to a tool whose
  only job is to render everything at once;
* the app imports these modules by FILE PATH (`nav_registry.load_dev_page`), the
  same way it loads the other optional dev tools, so nothing here can become an
  accidental import of the package.

Modules here import `farever_companion.*` absolutely (not relatively): they are
loaded from outside the package, so there is no package context to be relative
to.
"""
