using System;
using Barry.Theme;
using NUnit.Framework;
using UnityEngine;

namespace Barry.Environment.Tests
{
    public sealed class EnvironmentMeshFactoryTests
    {
        [Test]
        public void Floor_IsSingleCheapQuadAtRequestedSize()
        {
            Mesh mesh = EnvironmentMeshFactory.CreateFloor(20f);
            try
            {
                Assert.That(mesh.vertexCount, Is.EqualTo(4));
                Assert.That(mesh.triangles.Length, Is.EqualTo(6));
                Assert.That(mesh.bounds.size.x, Is.EqualTo(20f).Within(0.001f));
                Assert.That(mesh.bounds.size.z, Is.EqualTo(20f).Within(0.001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void PlatformDisc_HasCentreAndOneVertexPerSegment()
        {
            Mesh mesh = EnvironmentMeshFactory.CreateDisc(32, 1.25f);
            try
            {
                Assert.That(mesh.vertexCount, Is.EqualTo(33));
                Assert.That(mesh.triangles.Length, Is.EqualTo(96));
                Assert.That(mesh.uv[0], Is.EqualTo(new Vector2(0.5f, 0.5f)));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void Planet_RemainsLowPoly()
        {
            Mesh mesh = EnvironmentMeshFactory.CreateUvSphere(16, 8);
            try
            {
                Assert.That(mesh.vertexCount, Is.EqualTo(153));
                Assert.That(mesh.triangles.Length / 3, Is.EqualTo(256));
                Assert.That(mesh.vertexCount, Is.LessThan(200));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void Ring_IsAnAnnulusWithoutCentreOverdraw()
        {
            Mesh mesh = EnvironmentMeshFactory.CreateAnnulus(32);
            try
            {
                Assert.That(mesh.vertexCount, Is.EqualTo(64));
                Assert.That(mesh.triangles.Length / 3, Is.EqualTo(64));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void InvalidSegmentCounts_AreRejected()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => EnvironmentMeshFactory.CreateDisc(2));
            Assert.Throws<ArgumentOutOfRangeException>(() => EnvironmentMeshFactory.CreateAnnulus(2));
            Assert.Throws<ArgumentOutOfRangeException>(() => EnvironmentMeshFactory.CreateUvSphere(2, 8));
        }

        [Test]
        public void EnvironmentCreate_RequiresThemeTokens()
        {
            Assert.Throws<ArgumentNullException>(() => EnvironmentView.Create(null, (ThemeTokens)null));
        }

        [TestCase("ProceduralSkybox", "Barry/Environment/ProceduralSkybox")]
        [TestCase("FloorGrid", "Barry/Environment/FloorGrid")]
        [TestCase("PlatformDisc", "Barry/Environment/PlatformDisc")]
        [TestCase("Planet", "Barry/Environment/Planet")]
        [TestCase("Ring", "Barry/Environment/Ring")]
        public void RuntimeShaderResource_IsLoadable(string resourceName, string shaderName)
        {
            Shader shader = Resources.Load<Shader>(resourceName);
            Assert.That(shader, Is.Not.Null, resourceName + " must be retained in player builds");
            Assert.That(shader.name, Is.EqualTo(shaderName));
        }

        [Test]
        public void PlanetPlacement_HasRequestedDirectionDistanceAndAngularRadius()
        {
            const float distance = 30f;
            Vector3 eye = new Vector3(0f, 1.6f, 0f);
            Vector3 position = EnvironmentPlacement.FromEye(38f, 24.5f, distance);
            float scale = EnvironmentPlacement.ScaleForAngularRadius(2.7f, distance);
            Vector3 direction = position - eye;

            float azimuth = Mathf.Atan2(direction.x, direction.z) * Mathf.Rad2Deg;
            float elevation = Mathf.Asin(direction.y / direction.magnitude) * Mathf.Rad2Deg;
            float angularRadius = Mathf.Atan(scale / direction.magnitude) * Mathf.Rad2Deg;

            Assert.That(direction.magnitude, Is.EqualTo(distance).Within(0.001f));
            Assert.That(azimuth, Is.EqualTo(38f).Within(0.001f));
            Assert.That(elevation, Is.EqualTo(24.5f).Within(0.001f));
            Assert.That(angularRadius, Is.EqualTo(2.7f).Within(0.001f));
        }

        [Test]
        public void MoonScale_HasHalfDegreeAngularRadius()
        {
            const float distance = 30f;
            float scale = EnvironmentPlacement.ScaleForAngularRadius(0.5f, distance);
            Assert.That(Mathf.Atan(scale / distance) * Mathf.Rad2Deg,
                Is.EqualTo(0.5f).Within(0.001f));
        }
    }
}
