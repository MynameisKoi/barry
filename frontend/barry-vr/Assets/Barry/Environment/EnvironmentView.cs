using System;
using System.Collections.Generic;
using Barry.Theme;
using UnityEngine;

namespace Barry.Environment
{
    public static class EnvironmentPlacement
    {
        public static Vector3 FromEye(
            float azimuthDegrees,
            float elevationDegrees,
            float distance,
            float eyeHeight = 1.6f)
        {
            float azimuth = azimuthDegrees * Mathf.Deg2Rad;
            float elevation = elevationDegrees * Mathf.Deg2Rad;
            float horizontalDistance = Mathf.Cos(elevation) * distance;
            return new Vector3(
                Mathf.Sin(azimuth) * horizontalDistance,
                eyeHeight + Mathf.Sin(elevation) * distance,
                Mathf.Cos(azimuth) * horizontalDistance);
        }

        public static float ScaleForAngularRadius(float angularRadiusDegrees, float distance) =>
            Mathf.Tan(angularRadiusDegrees * Mathf.Deg2Rad) * distance;
    }

    [DisallowMultipleComponent]
    public sealed class EnvironmentView : MonoBehaviour
    {
        private readonly List<Material> ownedMaterials = new List<Material>();
        private readonly List<Mesh> ownedMeshes = new List<Mesh>();
        private Material skyMaterial;
        private Material floorMaterial;
        private Material platformMaterial;
        private Material planetMaterial;
        private Material ringMaterial;
        private Material moonMaterial;
        private Material previousSkybox;

        public static EnvironmentView Create(Transform parent, ThemeTokens tokens)
        {
            if (tokens == null) throw new ArgumentNullException(nameof(tokens));
            var root = new GameObject("Barry Environment");
            if (parent != null) root.transform.SetParent(parent, false);
            EnvironmentView view = root.AddComponent<EnvironmentView>();
            view.Build();
            view.Apply(tokens);
            return view;
        }

        private void OnEnable()
        {
            ThemeApplier.Changed += Apply;
            if (skyMaterial != null && ThemeApplier.Current != null) Apply(ThemeApplier.Current);
        }

        private void OnDisable()
        {
            ThemeApplier.Changed -= Apply;
        }

        public void Apply(ThemeTokens tokens)
        {
            if (tokens == null || skyMaterial == null) return;
            skyMaterial.SetColor("_SkyColor", tokens.envSky);
            skyMaterial.SetColor("_NebulaA", tokens.nebulaA);
            skyMaterial.SetColor("_NebulaB", tokens.nebulaB);
            skyMaterial.SetColor("_StarColor", tokens.starColor);
            skyMaterial.SetFloat("_StarMinAlpha", tokens.starMinAlpha);

            floorMaterial.SetColor("_FloorColor", tokens.floor);
            floorMaterial.SetColor("_GridColor", tokens.floorGrid);
            platformMaterial.SetColor("_FillColor", tokens.platformFill);
            platformMaterial.SetColor("_RimColor", tokens.platformRim);
            platformMaterial.SetColor("_DashedRingColor", tokens.platformRingDashed);
            planetMaterial.SetColor("_LightColor", tokens.planetLight);
            planetMaterial.SetColor("_MidColor", tokens.planetMid);
            planetMaterial.SetColor("_DarkColor", tokens.planetDark);
            ringMaterial.SetColor("_Color", tokens.planetRing);
            moonMaterial.SetColor("_Color", new Color32(0xE9, 0xE4, 0xFF, 217));
        }

        private void Build()
        {
            previousSkybox = RenderSettings.skybox;
            skyMaterial = CreateMaterial("ProceduralSkybox");
            floorMaterial = CreateMaterial("FloorGrid");
            platformMaterial = CreateMaterial("PlatformDisc");
            planetMaterial = CreateMaterial("Planet");
            ringMaterial = CreateMaterial("Ring");
            moonMaterial = CreateMaterial("Ring");
            RenderSettings.skybox = skyMaterial;

            CreateMeshObject("Floor", EnvironmentMeshFactory.CreateFloor(), floorMaterial,
                new Vector3(0f, 0f, 5f), Quaternion.identity, Vector3.one);
            CreateMeshObject("Operator Platform", EnvironmentMeshFactory.CreateDisc(), platformMaterial,
                new Vector3(0f, 0.005f, 0f), Quaternion.identity, Vector3.one);

            const float celestialDistance = 30f;
            float planetScale = EnvironmentPlacement.ScaleForAngularRadius(2.7f, celestialDistance);
            Vector3 planetPosition = EnvironmentPlacement.FromEye(38f, 24.5f, celestialDistance);
            CreateMeshObject("Ringed Planet", EnvironmentMeshFactory.CreateUvSphere(), planetMaterial,
                planetPosition, Quaternion.identity, Vector3.one * planetScale);
            CreateMeshObject("Planet Ring", EnvironmentMeshFactory.CreateAnnulus(64, 1.62f, 1.87f), ringMaterial,
                planetPosition, Quaternion.Euler(11f, 0f, -14f), Vector3.one * planetScale);

            float moonScale = EnvironmentPlacement.ScaleForAngularRadius(0.5f, celestialDistance);
            Vector3 moonPosition = EnvironmentPlacement.FromEye(-40f, 25f, celestialDistance);
            CreateMeshObject("Pale Moon", EnvironmentMeshFactory.CreateUvSphere(12, 6), moonMaterial,
                moonPosition, Quaternion.identity, Vector3.one * moonScale);
        }

        private Material CreateMaterial(string resourceName)
        {
            // Shaders live under Environment/Resources so Unity retains them in
            // Quest player builds; an unreferenced Shader.Find target is stripped.
            Shader shader = Resources.Load<Shader>(resourceName);
            if (shader == null) throw new InvalidOperationException("Missing environment shader resource: " + resourceName);
            var material = new Material(shader) { name = resourceName + " (Runtime)" };
            ownedMaterials.Add(material);
            return material;
        }

        private void CreateMeshObject(
            string objectName, Mesh mesh, Material material,
            Vector3 localPosition, Quaternion localRotation, Vector3 localScale)
        {
            ownedMeshes.Add(mesh);
            var child = new GameObject(objectName, typeof(MeshFilter), typeof(MeshRenderer));
            child.transform.SetParent(transform, false);
            child.transform.localPosition = localPosition;
            child.transform.localRotation = localRotation;
            child.transform.localScale = localScale;
            child.GetComponent<MeshFilter>().sharedMesh = mesh;
            child.GetComponent<MeshRenderer>().sharedMaterial = material;
        }

        private void OnDestroy()
        {
            ThemeApplier.Changed -= Apply;
            if (RenderSettings.skybox == skyMaterial) RenderSettings.skybox = previousSkybox;
            for (int i = 0; i < ownedMaterials.Count; i++) Destroy(ownedMaterials[i]);
            for (int i = 0; i < ownedMeshes.Count; i++) Destroy(ownedMeshes[i]);
        }
    }
}
