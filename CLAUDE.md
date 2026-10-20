# odar

An Expo (SDK 57) app that runs inside **Native Go** — a prebuilt iOS shell
(Native Now) on the simulator beside this terminal. There is no native build
step: the shell already has a fixed set of native modules compiled in, and it
loads this project's JavaScript bundle.

## Packages: native is fixed, JavaScript is open

**Native modules — only the ones already here.** Every native module the shell
contains is already in `package.json`, pinned to the exact version compiled
into the shell. Use them freely. Never:

- add a package that ships native code (it is not in the shell; the app
  crashes the moment it loads that module),
- upgrade, downgrade or remove any package already in `package.json`
  (`react`, `react-native`, `expo` and every `expo-*` / `react-native-*` / `@…`
  module must stay exactly as pinned),
- run `npx expo install --fix`, `expo prebuild`, `pod install`, or create
  `ios/` / `android/` folders.

A package ships native code if, once installed, its folder in `node_modules`
has any of: an `ios/` directory, a `*.podspec`, `expo-module.config.json`, or
a `codegenConfig` in its `package.json`. If a feature needs a native module
that is not here, say so instead of adding one — and prefer the ones that are
(e.g. `expo-image`, `react-native-reanimated`, `react-native-svg`,
`@shopify/react-native-skia`, `react-native-mmkv`, `expo-haptics`).

**JavaScript-only packages — allowed.** Pure JS libraries (state, dates,
validation, formatting, utilities, UI built on the native modules above…) run
fine in the shell. Install them with an exact version:

```sh
npm install --save-exact <package>
```

Then check nothing native slipped in and no pinned version moved:

```sh
native-now doctor      # must end with 0 errors, 0 warnings
git diff package.json  # only your new line(s)
```

If a JS package pulls in a native dependency, or bumps one of the pinned
ones, uninstall it and pick another.

## Code

- Entry point: `index.ts` → `src/App.tsx`.
- Style with NativeWind (`className`, Tailwind classes) or `StyleSheet`.

## Seeing your changes

When you finish a turn with changed files, a hook re-exports the app and
pushes it to the simulator, which reloads in place. If the new code throws,
the device's error comes back to you — fix it. On success you may get a
screenshot of the simulator to check the result against what was asked.

## Skills

- **native-now-modules** — every native module compiled into the shell, by
  category, with versions and docs. Read it before picking a library: cameras,
  maps, Skia, audio, video, databases, sensors, SwiftUI controls and more are
  already on the device — import them, no install or native build.
- **herdr** — this terminal is herdr: panes, tabs, running servers and other
  agents beside you.
- **ios-simulator**, **agent-device** — look at and drive the app on the
  simulator (snapshot the UI, tap, type, scroll, screenshot). The simulator is
  already connected; no setup needed.
- **dogfood** — explore the app on the simulator to find bugs and UX issues.
