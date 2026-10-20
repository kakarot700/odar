module.exports = function (api) {
  api.cache(true);
  return {
    // babel-preset-expo (SDK 57) registers react-native-worklets/plugin
    // (Reanimated 4) on its own; adding it again breaks the build.
    // NativeWind routes JSX through its runtime so `className` works.
    presets: [['babel-preset-expo', { jsxImportSource: 'nativewind' }], 'nativewind/babel'],
  };
};
