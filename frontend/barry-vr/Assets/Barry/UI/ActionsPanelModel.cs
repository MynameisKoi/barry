using System;
using System.Collections.Generic;
using Barry.Api;
using Barry.Teleop;

namespace Barry.UI
{
    public sealed class ActionPanelItemModel
    {
        public int Id { get; }
        public string Name { get; }
        public bool IsEnabled { get; internal set; }
        public bool IsHovered { get; internal set; }
        public bool IsRunning { get; internal set; }

        internal ActionPanelItemModel(int id, string name)
        {
            Id = id;
            Name = name;
        }
    }

    public sealed class ActionsPanelModel
    {
        public const string Footer = "Enabled only while sticks are centred. One action at a time.";
        public const string CollapsedTab = "ACTIONS · B";

        private readonly List<ActionPanelItemModel> items = new List<ActionPanelItemModel>();
        private bool controlsAvailable;
        private int? hoveredActionId;
        private int? runningActionId;

        public IReadOnlyList<ActionPanelItemModel> Items => items;
        public bool IsCollapsed { get; private set; }
        public int? RunningActionId => runningActionId;

        private ActionsPanelModel(ActionsResponse response)
        {
            UpdateActions(response);
        }

        public void UpdateActions(ActionsResponse response)
        {
            items.Clear();
            if (response?.actions == null)
            {
                RefreshItems();
                return;
            }
            var seenIds = new HashSet<int>();
            foreach (ActionInfo action in response.actions)
            {
                if (action == null || action.id <= 0 || string.IsNullOrWhiteSpace(action.name)) continue;
                if (seenIds.Add(action.id)) items.Add(new ActionPanelItemModel(action.id, action.name));
            }
            // Keep runningActionId across API polls. If the running item is
            // temporarily absent, it still blocks a second action until completion.
            RefreshItems();
        }

        public static ActionsPanelModel Create(
            ActionsResponse response,
            MotionCommand command,
            TeleopState teleopState,
            bool hasControl,
            bool collapsed = false)
        {
            var model = new ActionsPanelModel(response) { IsCollapsed = collapsed };
            model.UpdateAvailability(command, teleopState, hasControl);
            return model;
        }

        public void UpdateAvailability(MotionCommand command, TeleopState teleopState, bool hasControl)
        {
            controlsAvailable = hasControl && command.IsCentered && teleopState != TeleopState.Driving;
            RefreshItems();
        }

        public void SetCollapsed(bool collapsed)
        {
            IsCollapsed = collapsed;
            if (collapsed) hoveredActionId = null;
            RefreshItems();
        }

        public void SetHovered(int? actionId)
        {
            hoveredActionId = actionId.HasValue && Find(actionId.Value) != null
                ? actionId
                : null;
            RefreshItems();
        }

        public bool TryStartAction(int actionId)
        {
            ActionPanelItemModel item = Find(actionId);
            if (item == null || !item.IsEnabled || runningActionId.HasValue) return false;
            runningActionId = actionId;
            hoveredActionId = null;
            RefreshItems();
            return true;
        }

        public bool CompleteAction(int actionId)
        {
            if (runningActionId != actionId) return false;
            runningActionId = null;
            RefreshItems();
            return true;
        }

        private ActionPanelItemModel Find(int actionId)
        {
            for (int i = 0; i < items.Count; i++)
            {
                if (items[i].Id == actionId) return items[i];
            }
            return null;
        }

        private void RefreshItems()
        {
            bool canStart = controlsAvailable && !runningActionId.HasValue && !IsCollapsed;
            for (int i = 0; i < items.Count; i++)
            {
                ActionPanelItemModel item = items[i];
                item.IsRunning = runningActionId == item.Id;
                item.IsEnabled = canStart;
                item.IsHovered = canStart && hoveredActionId == item.Id;
            }
        }
    }
}
