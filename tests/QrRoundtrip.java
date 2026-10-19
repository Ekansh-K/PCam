import java.awt.image.BufferedImage;
import java.io.File;
import javax.imageio.ImageIO;
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;

public final class QrRoundtrip {
    public static void main(String[] args) throws Exception {
        BufferedImage image = ImageIO.read(new File(args[0]));
        int width=image.getWidth(), height=image.getHeight();
        byte[] gray = new byte[width*height];
        for (int y=0;y<height;y++) for(int x=0;x<width;x++) gray[y*width+x]=(byte)(image.getRGB(x,y)&255);
        PlanarYUVLuminanceSource source = new PlanarYUVLuminanceSource(gray,width,height,0,0,width,height,false);
        System.out.print(new QRCodeReader().decode(new BinaryBitmap(new HybridBinarizer(source))).getText());
    }
}
