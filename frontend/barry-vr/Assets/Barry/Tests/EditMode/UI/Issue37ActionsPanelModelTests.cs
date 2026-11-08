using Barry.Api;
using Barry.Teleop;
using Barry.UI;
using NUnit.Framework;

namespace Barry.Tests.EditMode.UI
{
    public sealed class Issue37ActionsPanelModelTests
    {
        [Test]
        public void Create_MapsValidApiActionsInOrder()
        {
            ActionsPanelModel model = CreateModel(MotionCommand.Zero, TeleopState.Armed);

            Assert.That(model.Items.Count, Is.EqualTo(2));
            Assert.That(model.Items[0].Id, Is.EqualTo(1));
            Assert.That(model.Items[0].Name, Is.EqualTo("Wave"));
            Assert.That(model.Items[1].Id, Is.EqualTo(2));
            Assert.That(model.Items[1].Name, Is.EqualTo("Sit"));
        }

        [Test]
        public void Items_EnableOnlyWhenCentredAndNotDriving()
        {
            ActionsPanelModel deflected = CreateModel(
                new MotionCommand(0.1f, 0f, 0f), TeleopState.Armed);
            ActionsPanelModel driving = CreateModel(MotionCommand.Zero, TeleopState.Driving);
            ActionsPanelModel stationary = CreateModel(MotionCommand.Zero, TeleopState.Armed);

            Assert.That(deflected.Items[0].IsEnabled, Is.False);
            Assert.That(driving.Items[0].IsEnabled, Is.False);
            Assert.That(stationary.Items[0].IsEnabled, Is.True);
        }

        [Test]
        public void Hover_AppliesOnlyToEnabledItem()
        {
            ActionsPanelModel model = CreateModel(MotionCommand.Zero, TeleopState.Armed);
            model.SetHovered(2);
            Assert.That(model.Items[0].IsHovered, Is.False);
            Assert.That(model.Items[1].IsHovered, Is.True);

            model.UpdateAvailability(new MotionCommand(0f, 0f, 0.2f), TeleopState.Armed, true);
            Assert.That(model.Items[1].IsHovered, Is.False);
        }

        [Test]
        public void Items_AreDisabledWithoutControlForViewOnlyOperator()
        {
            ActionsPanelModel model = CreateModel(
                MotionCommand.Zero, TeleopState.Armed, false);

            Assert.That(model.Items[0].IsEnabled, Is.False);
            Assert.That(model.Items[1].IsEnabled, Is.False);
            Assert.That(model.TryStartAction(1), Is.False);
        }

        [Test]
        public void RunningAction_DisablesEveryItemAndEnforcesOneAtATime()
        {
            ActionsPanelModel model = CreateModel(MotionCommand.Zero, TeleopState.Armed);

            Assert.That(model.TryStartAction(1), Is.True);
            Assert.That(model.RunningActionId, Is.EqualTo(1));
            Assert.That(model.Items[0].IsRunning, Is.True);
            Assert.That(model.Items[0].IsEnabled, Is.False);
            Assert.That(model.Items[1].IsEnabled, Is.False);
            Assert.That(model.TryStartAction(2), Is.False);
        }

        [Test]
        public void CompletingRunningAction_ReenablesItemsWhenStationary()
        {
            ActionsPanelModel model = CreateModel(MotionCommand.Zero, TeleopState.Disarmed);
            model.TryStartAction(1);

            Assert.That(model.CompleteAction(2), Is.False);
            Assert.That(model.CompleteAction(1), Is.True);
            Assert.That(model.RunningActionId, Is.Null);
            Assert.That(model.Items[0].IsEnabled, Is.True);
            Assert.That(model.Items[1].IsEnabled, Is.True);
        }

        [Test]
        public void CollapsedPanel_UsesRequiredCopyAndDisablesInteraction()
        {
            ActionsPanelModel model = CreateModel(MotionCommand.Zero, TeleopState.Armed);
            model.SetCollapsed(true);

            Assert.That(ActionsPanelModel.CollapsedTab, Is.EqualTo("ACTIONS · B"));
            Assert.That(ActionsPanelModel.Footer,
                Is.EqualTo("Enabled only while sticks are centred. One action at a time."));
            Assert.That(model.IsCollapsed, Is.True);
            Assert.That(model.Items[0].IsEnabled, Is.False);
        }

        [Test]
        public void InvalidAndDuplicateApiItems_AreIgnored()
        {
            var response = new ActionsResponse
            {
                actions = new[]
                {
                    new ActionInfo { id = 1, name = "Wave" },
                    new ActionInfo { id = 1, name = "Duplicate" },
                    new ActionInfo { id = 0, name = "Invalid" },
                    new ActionInfo { id = 3, name = "" }
                }
            };

            ActionsPanelModel model = ActionsPanelModel.Create(
                response, MotionCommand.Zero, TeleopState.Armed, true);

            Assert.That(model.Items.Count, Is.EqualTo(1));
            Assert.That(model.Items[0].Name, Is.EqualTo("Wave"));
        }

        [Test]
        public void UpdateActions_RefreshesItemsWithoutDroppingRunningState()
        {
            ActionsPanelModel model = CreateModel(MotionCommand.Zero, TeleopState.Armed);
            Assert.That(model.TryStartAction(1), Is.True);
            var refreshed = new ActionsResponse
            {
                actions = new[]
                {
                    new ActionInfo { id = 1, name = "Wave updated" },
                    new ActionInfo { id = 3, name = "Stand" }
                }
            };

            model.UpdateActions(refreshed);

            Assert.That(model.RunningActionId, Is.EqualTo(1));
            Assert.That(model.Items[0].Name, Is.EqualTo("Wave updated"));
            Assert.That(model.Items[0].IsRunning, Is.True);
            Assert.That(model.Items[1].IsEnabled, Is.False);
        }

        private static ActionsPanelModel CreateModel(
            MotionCommand command,
            TeleopState state,
            bool hasControl = true)
        {
            var response = new ActionsResponse
            {
                actions = new[]
                {
                    new ActionInfo { id = 1, name = "Wave" },
                    new ActionInfo { id = 2, name = "Sit" }
                }
            };
            return ActionsPanelModel.Create(response, command, state, hasControl);
        }
    }
}
