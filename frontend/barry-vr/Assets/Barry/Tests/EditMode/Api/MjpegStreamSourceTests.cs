using System.Text;
using Barry.Api;
using NUnit.Framework;

namespace Barry.Tests.EditMode.Api
{
    public sealed class MjpegStreamSourceTests
    {
        [Test]
        public void Parser_ReassemblesFrameAcrossChunks()
        {
            var parser = new MultipartJpegParser();
            byte[] first = Encoding.ASCII.GetBytes("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 8\r\n\r\n");
            byte[] jpegStart = { 0xff, 0xd8, 1, 2 };
            byte[] jpegEnd = { 3, 4, 0xff, 0xd9, 13, 10 };

            Assert.That(parser.Append(first, first.Length), Is.False);
            Assert.That(parser.Append(jpegStart, jpegStart.Length), Is.False);
            Assert.That(parser.Append(jpegEnd, jpegEnd.Length), Is.True);
            Assert.That(parser.LatestFrame, Is.EqualTo(new byte[] { 0xff, 0xd8, 1, 2, 3, 4, 0xff, 0xd9 }));
        }

        [Test]
        public void Parser_HandlesBoundaryAndHeadersSplitAcrossChunks()
        {
            var parser = new MultipartJpegParser();
            byte[] first = Encoding.ASCII.GetBytes("--fra");
            byte[] second = Encoding.ASCII.GetBytes("me\r\nContent-Type: image/jpeg\r\nContent-Len");
            byte[] third = Encoding.ASCII.GetBytes("gth: 5\r\n\r\n");
            byte[] jpeg = { 0xff, 0xd8, 7, 0xff, 0xd9 };

            Assert.That(parser.Append(first, first.Length), Is.False);
            Assert.That(parser.Append(second, second.Length), Is.False);
            Assert.That(parser.Append(third, third.Length), Is.False);
            Assert.That(parser.Append(jpeg, jpeg.Length), Is.True);
            Assert.That(parser.LatestFrame, Is.EqualTo(jpeg));
        }

        [Test]
        public void Parser_ContentLengthIgnoresEmbeddedEndMarker()
        {
            var parser = new MultipartJpegParser();
            byte[] header = Encoding.ASCII.GetBytes("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 9\r\n\r\n");
            byte[] jpegWithThumbnailEnd = { 0xff, 0xd8, 1, 0xff, 0xd9, 2, 3, 0xff, 0xd9 };

            Assert.That(parser.Append(header, header.Length), Is.False);
            Assert.That(parser.Append(jpegWithThumbnailEnd, jpegWithThumbnailEnd.Length), Is.True);
            Assert.That(parser.LatestFrame, Is.EqualTo(jpegWithThumbnailEnd));
        }

        [Test]
        public void Parser_KeepsOnlyLatestCompleteFrame()
        {
            var parser = new MultipartJpegParser();
            byte[] stream = { 0xff, 0xd8, 1, 0xff, 0xd9, 0xff, 0xd8, 2, 0xff, 0xd9 };
            Assert.That(parser.Append(stream, stream.Length), Is.True);
            Assert.That(parser.LatestFrame, Is.EqualTo(new byte[] { 0xff, 0xd8, 2, 0xff, 0xd9 }));
        }

        [Test]
        public void Parser_PreservesSplitStartMarkerWhenTrimming()
        {
            var parser = new MultipartJpegParser(8);
            byte[] noise = { 1, 2, 3, 4, 5, 6, 7, 8, 0xff };
            Assert.That(parser.Append(noise, noise.Length), Is.False);
            byte[] remainder = { 0xd8, 9, 0xff, 0xd9 };
            Assert.That(parser.Append(remainder, remainder.Length), Is.True);
        }

        [Test]
        public void SnapshotFallback_DerivesEndpointAndUsesFiveHertzInterval()
        {
            Assert.That(
                MjpegStreamSource.DeriveSnapshotUrl("http://barry/api/v1/camera/stream?token=test"),
                Is.EqualTo("http://barry/api/v1/camera/snapshot?token=test"));
            Assert.That(MjpegStreamSource.SnapshotPollIntervalSeconds, Is.EqualTo(0.2f));
        }
    }
}
