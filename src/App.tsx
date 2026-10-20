import { StatusBar } from 'expo-status-bar';
import { Text, View } from 'react-native';

export default function App() {
  return (
    <View className="flex-1 items-center justify-center bg-white dark:bg-black">
      <Text className="text-2xl font-semibold text-black dark:text-white">odar</Text>
      <Text className="mt-2 text-base text-neutral-500">Ask the agent to build something.</Text>
      <StatusBar style="auto" />
    </View>
  );
}
