using System;
using UnityEngine;

namespace Barry.Environment
{
    public static class EnvironmentMeshFactory
    {
        public static Mesh CreateFloor(float size = 30f)
        {
            float half = size * 0.5f;
            var mesh = new Mesh { name = "Barry Floor Mesh" };
            mesh.vertices = new[]
            {
                new Vector3(-half, 0f, -half), new Vector3(half, 0f, -half),
                new Vector3(half, 0f, half), new Vector3(-half, 0f, half)
            };
            mesh.uv = new[] { Vector2.zero, Vector2.right, Vector2.one, Vector2.up };
            mesh.triangles = new[] { 0, 2, 1, 0, 3, 2 };
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }

        public static Mesh CreateDisc(int segments = 64, float radius = 1.25f)
        {
            if (segments < 3) throw new ArgumentOutOfRangeException(nameof(segments));
            var vertices = new Vector3[segments + 1];
            var uv = new Vector2[vertices.Length];
            var triangles = new int[segments * 3];
            vertices[0] = Vector3.zero;
            uv[0] = new Vector2(0.5f, 0.5f);
            for (int i = 0; i < segments; i++)
            {
                float angle = i * Mathf.PI * 2f / segments;
                vertices[i + 1] = new Vector3(Mathf.Cos(angle) * radius, 0f, Mathf.Sin(angle) * radius);
                uv[i + 1] = new Vector2(Mathf.Cos(angle), Mathf.Sin(angle)) * 0.5f + Vector2.one * 0.5f;
                int next = (i + 1) % segments;
                int triangle = i * 3;
                triangles[triangle] = 0;
                triangles[triangle + 1] = next + 1;
                triangles[triangle + 2] = i + 1;
            }
            return Build("Barry Platform Disc", vertices, uv, triangles);
        }

        public static Mesh CreateAnnulus(int segments = 64, float innerRadius = 0.72f, float outerRadius = 1.15f)
        {
            if (segments < 3) throw new ArgumentOutOfRangeException(nameof(segments));
            if (innerRadius <= 0f || outerRadius <= innerRadius) throw new ArgumentOutOfRangeException(nameof(outerRadius));
            var vertices = new Vector3[segments * 2];
            var uv = new Vector2[vertices.Length];
            var triangles = new int[segments * 6];
            for (int i = 0; i < segments; i++)
            {
                float angle = i * Mathf.PI * 2f / segments;
                Vector3 direction = new Vector3(Mathf.Cos(angle), 0f, Mathf.Sin(angle));
                vertices[i * 2] = direction * innerRadius;
                vertices[i * 2 + 1] = direction * outerRadius;
                uv[i * 2] = new Vector2((float)i / segments, 0f);
                uv[i * 2 + 1] = new Vector2((float)i / segments, 1f);
                int next = (i + 1) % segments;
                int triangle = i * 6;
                triangles[triangle] = i * 2;
                triangles[triangle + 1] = next * 2 + 1;
                triangles[triangle + 2] = i * 2 + 1;
                triangles[triangle + 3] = i * 2;
                triangles[triangle + 4] = next * 2;
                triangles[triangle + 5] = next * 2 + 1;
            }
            return Build("Barry Planet Ring", vertices, uv, triangles);
        }

        public static Mesh CreateUvSphere(int longitudeSegments = 16, int latitudeSegments = 8)
        {
            if (longitudeSegments < 3) throw new ArgumentOutOfRangeException(nameof(longitudeSegments));
            if (latitudeSegments < 2) throw new ArgumentOutOfRangeException(nameof(latitudeSegments));
            int stride = longitudeSegments + 1;
            var vertices = new Vector3[(latitudeSegments + 1) * stride];
            var uv = new Vector2[vertices.Length];
            for (int latitude = 0; latitude <= latitudeSegments; latitude++)
            {
                float v = (float)latitude / latitudeSegments;
                float phi = v * Mathf.PI;
                for (int longitude = 0; longitude <= longitudeSegments; longitude++)
                {
                    float u = (float)longitude / longitudeSegments;
                    float theta = u * Mathf.PI * 2f;
                    int index = latitude * stride + longitude;
                    vertices[index] = new Vector3(
                        Mathf.Sin(phi) * Mathf.Cos(theta),
                        Mathf.Cos(phi),
                        Mathf.Sin(phi) * Mathf.Sin(theta));
                    uv[index] = new Vector2(u, 1f - v);
                }
            }
            var triangles = new int[latitudeSegments * longitudeSegments * 6];
            int cursor = 0;
            for (int latitude = 0; latitude < latitudeSegments; latitude++)
            for (int longitude = 0; longitude < longitudeSegments; longitude++)
            {
                int a = latitude * stride + longitude;
                int b = a + stride;
                triangles[cursor++] = a;
                triangles[cursor++] = b;
                triangles[cursor++] = a + 1;
                triangles[cursor++] = a + 1;
                triangles[cursor++] = b;
                triangles[cursor++] = b + 1;
            }
            return Build("Barry Low Poly Planet", vertices, uv, triangles);
        }

        private static Mesh Build(string name, Vector3[] vertices, Vector2[] uv, int[] triangles)
        {
            var mesh = new Mesh { name = name, vertices = vertices, uv = uv, triangles = triangles };
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }
    }
}
