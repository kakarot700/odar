const { getDefaultConfig } = require('expo/metro-config');
const { withNativeWind } = require('nativewind/metro');

// NativeWind compiles `global.css` through Tailwind at bundle time; `genux
// export` runs `expo export` here, so the guest's Metro config is what applies.
module.exports = withNativeWind(getDefaultConfig(__dirname), { input: './global.css' });
