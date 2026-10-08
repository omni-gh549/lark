<script>
  import { getTriggerAction, getIsStreaming } from "@openuidev/svelte-lang";
  let { props } = $props();
  const trigger = getTriggerAction();
  const streaming = getIsStreaming();
</script>

<button type="button" disabled={streaming()} onclick={() => trigger(props.message?.trim() || props.label)}>{props.label}</button>

<style>
  button { align-self: flex-start; padding: 6px 14px; border-radius: 999px; background: var(--glass-strong); border: 1px solid var(--line); }
</style>
